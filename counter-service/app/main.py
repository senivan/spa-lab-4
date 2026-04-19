from __future__ import annotations

import asyncio
import os

import hazelcast
import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import BigInteger, Column, Integer, String, create_engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, sessionmaker

app = FastAPI(title="counter-service")

INSTANCE_NAME = os.getenv("INSTANCE_NAME", "counter-service")
SERVICE_NAME = os.getenv("SERVICE_NAME", "counter-service")
SERVICE_URL = os.getenv("SERVICE_URL", "http://counter-service:8002")
CONFIG_SERVER_URL = os.getenv("CONFIG_SERVER_URL", "http://config-server:8500")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://lab3:lab3@postgres:5432/lab3")
HZ_CLUSTER_NAME = os.getenv("HZ_CLUSTER_NAME", "dev")
HZ_CLUSTER_MEMBERS = [
    member.strip()
    for member in os.getenv("HZ_CLUSTER_MEMBERS", "hz1:5701,hz2:5701,hz3:5701").split(",")
    if member.strip()
]
COUNTER_QUEUE_NAME = os.getenv("COUNTER_QUEUE_NAME", "counter-tx-queue")
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
mq_client: hazelcast.HazelcastClient | None = None
counter_queue = None
consumer_task: asyncio.Task | None = None
shutdown_event = asyncio.Event()


class Base(DeclarativeBase):
    pass


class AccountBalance(Base):
    __tablename__ = "account_balances"

    user_id = Column(String, primary_key=True)
    balance = Column(BigInteger, nullable=False, default=0)


class Transaction(Base):
    __tablename__ = "transactions"
    
    transaction_id = Column(String, primary_key=True)
    timestamp = Column(String, nullable=False)
    user_id = Column(String, nullable=False, index=True)
    amount = Column(Integer, nullable=False)


class TxPayload(BaseModel):
    transaction_id: str
    timestamp: str
    user_id: str
    amount: int
    message: str | None = None


class ServiceRegistration(BaseModel):
    service_name: str
    service_url: str


async def _register_service() -> None:
    payload = ServiceRegistration(service_name=SERVICE_NAME, service_url=SERVICE_URL)
    last_error: Exception | None = None
    async with httpx.AsyncClient(timeout=5.0) as client:
        for _ in range(30):
            try:
                response = await client.post(f"{CONFIG_SERVER_URL}/register", json=payload.model_dump())
                response.raise_for_status()
                print(f"[{INSTANCE_NAME}] registered in config-server as {SERVICE_NAME} -> {SERVICE_URL}", flush=True)
                return
            except (httpx.HTTPError, OSError, TimeoutError) as exc:
                last_error = exc
                await asyncio.sleep(1)
    raise RuntimeError(f"could not register in config-server: {last_error}")


def _apply_transaction(tx: TxPayload) -> int:
    with SessionLocal() as session:
        with session.begin():
            session.add(
                Transaction(
                    transaction_id=tx.transaction_id,
                    timestamp=tx.timestamp,
                    user_id=tx.user_id,
                    amount=tx.amount,
                )
            )
            row = session.get(AccountBalance, tx.user_id)
            if row is None:
                row = AccountBalance(user_id=tx.user_id, balance=0)
                session.add(row)
                session.flush()
            row.balance += tx.amount
            new_balance = row.balance
    print(f"[{INSTANCE_NAME}] {tx.user_id} += {tx.amount} -> {new_balance}", flush=True)
    return new_balance


async def _consume_queue() -> None:
    if counter_queue is None:
        raise RuntimeError("message queue client not ready")
    while not shutdown_event.is_set():
        item = await asyncio.to_thread(counter_queue.poll, 1)
        if item is None:
            continue
        tx = TxPayload.model_validate(item)
        try:
            _apply_transaction(tx)
            print(f"[{INSTANCE_NAME}] consumed queued transaction {tx.transaction_id}", flush=True)
        except IntegrityError:
            print(f"[{INSTANCE_NAME}] skipped duplicate transaction {tx.transaction_id}", flush=True)
        except Exception as exc:
            print(f"[{INSTANCE_NAME}] failed to apply queued transaction {tx.transaction_id}: {exc}", flush=True)


@app.on_event("startup")
async def startup() -> None:
    shutdown_event.clear()
    last_db_error: Exception | None = None
    for _ in range(30):
        try:
            Base.metadata.create_all(bind=engine)
            last_db_error = None
            break
        except Exception as exc:
            last_db_error = exc
            await asyncio.sleep(1)
    if last_db_error is not None:
        raise RuntimeError(f"could not initialize database: {last_db_error}") from last_db_error
    global mq_client, counter_queue, consumer_task
    last_error: Exception | None = None
    for _ in range(60):
        try:
            mq_client = hazelcast.HazelcastClient(cluster_name=HZ_CLUSTER_NAME, cluster_members=HZ_CLUSTER_MEMBERS)
            counter_queue = mq_client.get_queue(COUNTER_QUEUE_NAME).blocking()
            break
        except (OSError, RuntimeError, TimeoutError) as exc:
            last_error = exc
            await asyncio.sleep(1)
    if counter_queue is None:
        raise RuntimeError(f"could not connect to Hazelcast queue: {last_error}")
    print(f"[{INSTANCE_NAME}] connected to Hazelcast queue {COUNTER_QUEUE_NAME}", flush=True)
    await _register_service()
    consumer_task = asyncio.create_task(_consume_queue())


@app.on_event("shutdown")
async def shutdown() -> None:
    shutdown_event.set()
    if consumer_task is not None:
        await consumer_task
    if mq_client is not None:
        mq_client.shutdown()


@app.post("/transactions/apply")
async def apply_transaction(tx: TxPayload):
    try:
        new_balance = _apply_transaction(tx)
        return {"user_id": tx.user_id, "balance": new_balance}
    except IntegrityError:
        with SessionLocal() as session:
            row = session.get(AccountBalance, tx.user_id)
            balance = row.balance if row is not None else 0
        return {"user_id": tx.user_id, "balance": balance, "duplicate": True}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/balance/{user_id}")
async def get_balance(user_id: str):
    with SessionLocal() as session:
        row = session.get(AccountBalance, user_id)
        balance = row.balance if row is not None else 0
    return {"user_id": user_id, "balance": balance}


@app.get("/balances")
async def get_all_balances():
    with SessionLocal() as session:
        rows = session.execute(select(AccountBalance)).scalars().all()
        return {row.user_id: row.balance for row in rows}


@app.post("/reset")
async def reset():
    with SessionLocal() as session:
        with session.begin():
            session.execute(text("TRUNCATE TABLE account_balances RESTART IDENTITY CASCADE"))
            session.execute(text("TRUNCATE TABLE transactions RESTART IDENTITY CASCADE"))
    return {"ok": True}
