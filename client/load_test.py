import argparse
import asyncio
import time

import httpx

FACADE = "http://localhost:8000"


async def _retry_ok(fn, tries=50, delay=0.2):
    last = None
    for _ in range(tries):
        try:
            return await fn()
        except (httpx.HTTPError, OSError, RuntimeError) as exc:
            last = exc
            await asyncio.sleep(delay)
    raise last


async def reset_system():
    async with httpx.AsyncClient(timeout=10.0) as client:
        await _retry_ok(lambda: client.get(f"{FACADE}/metrics"))
        await client.post(f"{FACADE}/metrics/reset")


async def run_client(user_id: str, n: int):
    async with httpx.AsyncClient(timeout=10.0) as client:
        for i in range(n):
            response = await client.post(
                f"{FACADE}/transaction",
                json={"user_id": user_id, "amount": 1, "message": f"msg{i + 1}"},
            )
            response.raise_for_status()


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--clients", type=int, default=10)
    ap.add_argument("--per-client", type=int, default=10_000)
    ap.add_argument("--scenario", type=int, choices=[1, 2], required=True)
    args = ap.parse_args()

    await reset_system()

    if args.scenario == 1:
        user_ids = [f"user{i}" for i in range(args.clients)]
    else:
        user_ids = ["same_user"] * args.clients

    t0 = time.perf_counter()
    await asyncio.gather(*(run_client(user_ids[i], args.per_client) for i in range(args.clients)))
    total_time = time.perf_counter() - t0

    total_requests = args.clients * args.per_client
    rps = total_requests / total_time

    async with httpx.AsyncClient(timeout=10.0) as client:
        metrics = (await client.get(f"{FACADE}/metrics")).json()

    print(f"scenario={args.scenario}")
    print(f"total_requests={total_requests}")
    print(f"total_time_sec={total_time:.4f}")
    print(f"requests_per_sec={rps:.2f}")
    print("facade metrics:", metrics)

    async with httpx.AsyncClient(timeout=10.0) as client:
        if args.scenario == 1:
            ok = True
            for uid in set(user_ids):
                bal = (await client.get(f"{FACADE}/user/{uid}")).json()["balance"]
                if bal != args.per_client:
                    ok = False
                    print(f"[FAIL] {uid} balance={bal} expected={args.per_client}")
            print("VALIDATION:", "OK" if ok else "FAIL")
        else:
            expected = args.clients * args.per_client
            bal = (await client.get(f"{FACADE}/user/same_user")).json()["balance"]
            print("VALIDATION:", "OK" if bal == expected else f"FAIL balance={bal} expected={expected}")


if __name__ == "__main__":
    asyncio.run(main())
