from __future__ import annotations

import asyncio
import os
import time

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from app.store import HazelcastStore

app = FastAPI(title="logging-service")

INSTANCE_NAME = os.getenv("INSTANCE_NAME", "logging")
SERVICE_NAME = os.getenv("SERVICE_NAME", "logging-service")
SERVICE_URL = os.getenv("SERVICE_URL", "http://logging-service:8001")
CONFIG_SERVER_URL = os.getenv("CONFIG_SERVER_URL", "http://config-server:8500")
HZ_CLUSTER_NAME = os.getenv("HZ_CLUSTER_NAME", "dev")
HZ_CLUSTER_MEMBERS = [
    member.strip()
    for member in os.getenv("HZ_CLUSTER_MEMBERS", "hz1:5701,hz2:5701,hz3:5701").split(",")
    if member.strip()
]

store: HazelcastStore | None = None


class Transaction(BaseModel):
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


@app.on_event("startup")
async def startup() -> None:
    global store
    last_error: Exception | None = None
    for _ in range(60):
        try:
            store = HazelcastStore(INSTANCE_NAME, HZ_CLUSTER_NAME, HZ_CLUSTER_MEMBERS)
            break
        except (OSError, RuntimeError, TimeoutError) as exc:
            last_error = exc
            time.sleep(1)
    if store is None:
        raise RuntimeError(f"could not connect to Hazelcast: {last_error}")
    print(f"[{INSTANCE_NAME}] connected to Hazelcast members={HZ_CLUSTER_MEMBERS}", flush=True)
    await _register_service()


@app.on_event("shutdown")
async def shutdown() -> None:
    if store is not None:
        store.close()


@app.post("/transactions")
async def store_transaction(tx: Transaction):
    if store is None:
        raise HTTPException(status_code=503, detail="hazelcast client not ready")
    store.save(tx.model_dump())
    return {"ok": True, "instance": INSTANCE_NAME, "transaction_id": tx.transaction_id}


@app.get("/transactions/user/{user_id}")
async def user_transactions(user_id: str):
    if store is None:
        raise HTTPException(status_code=503, detail="hazelcast client not ready")
    return store.get_by_user(user_id)


@app.get("/transactions")
async def all_transactions():
    if store is None:
        raise HTTPException(status_code=503, detail="hazelcast client not ready")
    return list(store.transactions_map.values())


@app.post("/reset")
async def reset():
    if store is None:
        raise HTTPException(status_code=503, detail="hazelcast client not ready")
    store.reset()
    return {"ok": True}
