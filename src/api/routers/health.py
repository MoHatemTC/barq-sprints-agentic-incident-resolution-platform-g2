from fastapi import Response, HTTPException, Depends, APIRouter
from src.api.dependencies import get_redis, get_db_session
from sqlalchemy import text

router = APIRouter()

# check the liveness of the application
@router.get("/health")
async def health_check():
    return Response(status_code=200, content="OK")

# Check the readiness of the application
@router.get("/ready")
async def readiness_check(redis_client = Depends(get_redis), db = Depends(get_db_session)):

    try:
        await redis_client.ping()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Redis not ready: {str(e)}")

    try:
        await db.execute(text("SELECT 1"))
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Database not ready: {str(e)}")

    return Response(status_code=200, content="Ready")