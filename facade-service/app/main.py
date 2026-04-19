from __future__ import annotations

import asyncio
import os
import random
import time
from typing import Any

import hazelcast
import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="facade-service")

INSTANCE_NAME = os.getenv("INSTANCE_NAME", "facade-service")
SERVICE_NAME = os.getenv("SERVICE_NAME", "facade-service")
SERVICE_URL = os.getenv("SERVICE_URL", "http://facade-service:8000")
CONFIG_SERVER_URL = os.getenv("CONFIG_SERVER_URL", "http://config-server:8500")
HZ_CLUSTER_NAME = os.getenv("HZ_CLUSTER_NAME", "dev")
HZ_CLUSTER_MEMBERS = [
    member.strip()
    for member in os.getenv("HZ_CLUSTER_MEMBERS", "hz1:5701,hz2:5701,hz3:5701").split(",")
    if member.strip()
]
COUNTER_QUEUE_NAME = os.getenv("COUNTER_QUEUE_NAME", "counter-tx-queue")

_metrics_lock = asyncio.Lock()
_metrics = {
    "logging_calls": 0,
    "counter_calls": 0,
    "logging_total_sec": 0.0,
    "counter_total_sec": 0.0,
}
mq_client: hazelcast.HazelcastClient | None = None
counter_queue = None


class ClientTx(BaseModel):
    user_id: str
    amount: int
    message: str | None = None


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


async def _service_urls(service_name: str) -> list[str]:
    async with httpx.AsyncClient(timeout=5.0) as client:
        response = await client.get(f"{CONFIG_SERVER_URL}/services/{service_name}")
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"config-server error for {service_name}: {response.text}")
    service_urls = response.json().get("service_urls", [])
    if not service_urls:
        raise HTTPException(status_code=503, detail=f"no registered instances for {service_name}")
    return service_urls


async def _random_service_url(service_name: str) -> str:
    return random.choice(await _service_urls(service_name))


def _queue_client():
    if counter_queue is None:
        raise HTTPException(status_code=503, detail="message queue client not ready")
    return counter_queue


async def _timed_call(kind: str, coro: Any):
    t0 = time.perf_counter()
    result = await coro
    elapsed = time.perf_counter() - t0
    async with _metrics_lock:
        if kind == "logging":
            _metrics["logging_calls"] += 1
            _metrics["logging_total_sec"] += elapsed
        else:
            _metrics["counter_calls"] += 1
            _metrics["counter_total_sec"] += elapsed
    return result


async def _post_logging(tx: Transaction) -> dict:
    last_error: (httpx.HTTPError | OSError | TimeoutError | None) = None
    service_urls = await _service_urls("logging-service")
    random.shuffle(service_urls)
    async with httpx.AsyncClient(timeout=5.0) as client:
        for base_url in service_urls:
            try:
                response = await _timed_call("logging", client.post(f"{base_url}/transactions", json=tx.model_dump()))
                if response.status_code < 400:
                    print(f"[{INSTANCE_NAME}] logging via {base_url}", flush=True)
                    return response.json()
                last_error = httpx.HTTPStatusError("logging-service error", request=response.request, response=response)
            except (httpx.HTTPError, OSError, TimeoutError) as exc:
                last_error = exc
    raise HTTPException(status_code=502, detail=f"all logging-service replicas failed: {last_error}")


async def _get_logging_transactions(user_id: str):
    last_error: (httpx.HTTPError | OSError | TimeoutError | None) = None
    service_urls = await _service_urls("logging-service")
    random.shuffle(service_urls)
    async with httpx.AsyncClient(timeout=5.0) as client:
        for base_url in service_urls:
            try:
                response = await _timed_call("logging", client.get(f"{base_url}/transactions/user/{user_id}"))
                if response.status_code < 400:
                    print(f"[{INSTANCE_NAME}] read transactions via {base_url}", flush=True)
                    return response.json()
                last_error = httpx.HTTPStatusError("logging-service error", request=response.request, response=response)
            except (httpx.HTTPError, OSError, TimeoutError) as exc:
                last_error = exc
    raise HTTPException(status_code=502, detail=f"all logging-service replicas failed: {last_error}")


@app.on_event("startup")
async def startup() -> None:
    global mq_client, counter_queue
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


@app.on_event("shutdown")
async def shutdown() -> None:
    if mq_client is not None:
        mq_client.shutdown()


@app.post("/transaction")
async def post_transaction(msg: ClientTx):
    tx_id = str(time.time_ns())
    ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
    tx = Transaction(transaction_id=tx_id, timestamp=ts, user_id=msg.user_id, amount=msg.amount, message=msg.message)

    log_resp = await _post_logging(tx)
    await asyncio.to_thread(_queue_client().put, tx.model_dump())
    print(f"[{INSTANCE_NAME}] queued transaction {tx.transaction_id} for counter-service", flush=True)

    return {
        "transaction_id": tx_id,
        "queued": True,
        "logging": log_resp
    }


@app.get("/user/{user_id}")
async def get_user(user_id: str):
    counter_url = await _random_service_url("counter-service")
    async with httpx.AsyncClient(timeout=5.0) as client:
        tx_resp = await _get_logging_transactions(user_id)
        try:
            bal_resp = await _timed_call("counter", client.get(f"{counter_url}/balance/{user_id}"))
        except (httpx.HTTPError, OSError, TimeoutError) as exc:
            return {
                "balance": None,
                "transactions": tx_resp,
                "counter_available": False,
                "counter_error": str(exc),
            }

    if bal_resp.status_code >= 400:
        return {
            "balance": None,
            "transactions": tx_resp,
            "counter_available": False,
            "counter_error": bal_resp.text,
        }

    return {
        "balance": bal_resp.json().get("balance", 0),
        "transactions": tx_resp,
        "counter_available": True,
    }


@app.get("/metrics")
async def get_metrics():
    async with _metrics_lock:
        metrics = dict(_metrics)
    metrics["logging_avg_ms"] = (metrics["logging_total_sec"] / metrics["logging_calls"] * 1000.0) if metrics["logging_calls"] else 0.0
    metrics["counter_avg_ms"] = (metrics["counter_total_sec"] / metrics["counter_calls"] * 1000.0) if metrics["counter_calls"] else 0.0
    return metrics


@app.post("/metrics/reset")
async def reset_metrics():
    async with _metrics_lock:
        _metrics["logging_calls"] = 0
        _metrics["counter_calls"] = 0
        _metrics["logging_total_sec"] = 0.0
        _metrics["counter_total_sec"] = 0.0
    return {"ok": True}
