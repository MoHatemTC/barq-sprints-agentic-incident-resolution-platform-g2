import asyncio
import json
import time
import httpx
import redis.asyncio as redis

AUTH_TOKEN = "local-dev-test-token-123"
REDIS_URL = "redis://localhost:6379/0"
API_URL = "http://localhost:8000/webhook"

async def prefill_queue(depth: int):
    """Simulates a backed-up queue by pushing junk directly, bypassing the app."""
    client = redis.from_url(REDIS_URL)
    for i in range(depth):
        await client.rpush("incident_events", json.dumps({"stub": i}))
    await client.close()

async def measure_latency_at_depth(depth: int, samples: int = 20):
    await prefill_queue(depth)
    latencies = []
    async with httpx.AsyncClient() as client:
        for _ in range(samples):
            payload = {
                "event_id": f"evt_{time.time_ns()}",
                "sys_id": "sys_load_test",
                "number": "INC000000",
                "event_type": "incident.created",
                "contract_version": "v1",
            }
            start = time.monotonic()
            await client.post(API_URL, json=payload, headers={"Authorization": f"Bearer {AUTH_TOKEN}"})
            latencies.append((time.monotonic() - start) * 1000)
    latencies.sort()
    p95 = latencies[int(len(latencies) * 0.95)]
    print(f"Queue depth {depth}: p95 = {p95:.2f}ms")

async def main():
    for depth in (0, 1000, 10000, 50000):
        await measure_latency_at_depth(depth)

if __name__ == "__main__":
    asyncio.run(main())