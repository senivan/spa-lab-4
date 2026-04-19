from __future__ import annotations

import asyncio

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="config-server")

registry_lock = asyncio.Lock()
registry: dict[str, list[str]] = {}


class ServiceRegistration(BaseModel):
    service_name: str
    service_url: str


@app.post("/register")
async def register_service(payload: ServiceRegistration):
    async with registry_lock:
        service_urls = registry.setdefault(payload.service_name, [])
        if payload.service_url not in service_urls:
            service_urls.append(payload.service_url)
    print(f"[config-server] registered {payload.service_name} -> {payload.service_url}", flush=True)
    return {"ok": True, "service_name": payload.service_name, "service_urls": registry[payload.service_name]}


@app.get("/services/{service_name}")
async def get_service(service_name: str):
    async with registry_lock:
        service_urls = list(registry.get(service_name, []))
    return {"service_name": service_name, "service_urls": service_urls}
