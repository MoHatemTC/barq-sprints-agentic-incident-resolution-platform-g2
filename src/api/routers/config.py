from pydantic import BaseModel
from fastapi import Depends, APIRouter
from src.api.dependencies import get_settings

router = APIRouter()

class ConfigResponse(BaseModel):
    postgres_host: str
    postgres_port: int
    redis_host: str
    redis_port: int
    # secrets deliberately excluded — never postgres_password, redis_password, webhook_auth_token

@router.get("/api/v1/config")
async def get_config(settings = Depends(get_settings)):
    return ConfigResponse(
        postgres_host=settings.postgres_host,
        postgres_port=settings.postgres_port,
        redis_host=settings.redis_host,
        redis_port=settings.redis_port,
    )