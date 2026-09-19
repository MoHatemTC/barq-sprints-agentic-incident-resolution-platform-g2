from fastapi import Request, Header, HTTPException, Depends
from src.api.schemas import Settings
from functools import lru_cache

@lru_cache
def get_settings() -> Settings:
    return Settings()

def get_redis(request: Request):
    return request.app.state.redis

async def get_db_session(request: Request):
    async with request.app.state.async_session() as session:
        yield session