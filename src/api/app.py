import redis.asyncio as redis
import logging

from pythonjsonlogger import jsonlogger
from fastapi import FastAPI
from fastapi.exceptions import HTTPException
from src.api.exceptions import unhandled_exception_handler
from src.api.schemas import Base
from contextlib import asynccontextmanager
from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from src.api.dependencies import get_settings
from src.api.routers import approvals, dlq, webhook, health, config, executions, eval
from src.api.middleware import CorrelationIDMiddleware, LangfuseTracingMiddleware
from src.api.exceptions import http_exception_handler

load_dotenv()

handler = logging.StreamHandler()
handler.setFormatter(jsonlogger.JsonFormatter("%(asctime)s %(levelname)s %(message)s %(correlation_id)s %(method)s %(path)s %(status_code)s %(duration_ms)s"))
logging.getLogger("incident.api").addHandler(handler)
logging.getLogger("incident.api").setLevel(logging.INFO)

@asynccontextmanager
async def lifespan(app: FastAPI):

    settings = get_settings()  # Load settings from environment variables
    app.state.redis = redis.from_url(settings.redis_url) # Initialize Redis client with the URL from settings
    app.state.engine = create_async_engine(settings.database_url) # Initialize SQLAlchemy engine with the database URL from settings

    #delete later
    async with app.state.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    app.state.async_session = async_sessionmaker(app.state.engine, expire_on_commit=False) # Create an async session maker for database sessions

    yield

    await app.state.redis.close()  # cleanup on shutdown
    await app.state.engine.dispose()  # cleanup on shutdown

def create_app() -> FastAPI:

    app = FastAPI(lifespan=lifespan)

    app.add_middleware(CorrelationIDMiddleware)
    app.add_middleware(LangfuseTracingMiddleware)
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)

    app.include_router(webhook.router)
    app.include_router(health.router)
    app.include_router(config.router)
    app.include_router(executions.router)
    app.include_router(approvals.router)
    app.include_router(dlq.router)
    app.include_router(eval.router)

    return app


app = create_app()  #registers the lifespan event handler