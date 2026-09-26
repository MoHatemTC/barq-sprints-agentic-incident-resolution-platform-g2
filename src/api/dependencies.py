from fastapi import Request, Depends
from src.api.schemas import Settings
from functools import lru_cache

class RedisIncidentProducer:

    """
    Publishes accepted incidents onto the shared Redis list `incident_events`,
    consumed by S2.3's redis_consumer.py via BLPOP. This is the real,
    confirmed integration point between S2.1 and S2.3 — not a placeholder.
    """

    def __init__(self, redis_client):
        self._redis = redis_client

    async def enqueue(self, payload: str):
        await self._redis.rpush("incident_events", payload)

def get_redis_producer(request: Request) -> RedisIncidentProducer:
    return RedisIncidentProducer(request.app.state.redis)

@lru_cache
def get_settings() -> Settings:
    return Settings()

class ServiceNowClient:

    """
    Placeholder for on-demand incident detail fetches by the Celery worker.
    Not yet called by any S2.1 route — added so the DI surface matches
    the platform's provider pattern ahead of worker-side usage.
    """

    def __init__(self, base_url: str, auth_token: str):
        self._base_url = base_url
        self._auth_token = auth_token

def get_servicenow_client(settings: Settings = Depends(get_settings)) -> ServiceNowClient:
    raise NotImplementedError("ServiceNow client wiring lands with worker integration")


def get_redis(request: Request):
    return request.app.state.redis

async def get_db_session(request: Request):
    async with request.app.state.async_session() as session:
        yield session