import os, certifi
os.environ["SSL_CERT_FILE"] = certifi.where()
import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from langfuse import get_client

langfuse = get_client()

class LangfuseTracingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        correlation_id = getattr(request.state, "correlation_id", None)
        with langfuse.start_as_current_observation(
            as_type="span",
            name=f"{request.method} {request.url.path}",
        ) as span:
            span.update(metadata={"correlation_id": correlation_id})
            response = await call_next(request)
            span.update(output={"status_code": response.status_code})
        return response

logger = logging.getLogger("incident.api")
logging.basicConfig(level=logging.INFO)

class CorrelationIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        correlation_id = request.headers.get("X-Correlation-ID") or str(uuid.uuid4())
        request.state.correlation_id = correlation_id
        start = time.monotonic()

        response = await call_next(request)

        duration_ms = round((time.monotonic() - start) * 1000, 2)
        response.headers["X-Correlation-ID"] = correlation_id

        logger.info(
            "",
            extra={
                "correlation_id": correlation_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
            },
        )

        return response