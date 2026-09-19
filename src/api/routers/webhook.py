from fastapi import Response, HTTPException, Depends, APIRouter
from src.api.schemas import Payload
from sqlalchemy.exc import IntegrityError
from src.api.dependencies import get_redis, get_db_session
from src.api.auth import verify_token
from src.db.models import Event

router = APIRouter()

SUPPORTED_CONTRACT_VERSIONS = {"v1"}

#checks payload and return 202 quickly
@router.post("/webhook")
async def webhook(ticket: Payload, redis_client = Depends(get_redis), authenticated: None = Depends(verify_token), db = Depends(get_db_session)):

    #reject unknown contract versions distinctly, before doing anything else
    if ticket.contract_version not in SUPPORTED_CONTRACT_VERSIONS:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported Contract Version: {ticket.contract_version}"
        )

    # Create a new Event instance
    event = Event(
        event_identifier=ticket.event_id,
        incident_sys_id=ticket.sys_id,
        incident_number=ticket.number,
        event_type=ticket.event_type,
        contract_version=ticket.contract_version
    )

    try:
        db.add(event)
        await db.commit()
    except IntegrityError:
        await db.rollback()
        return Response(status_code=202, content="Duplicate, already processed")

    #enqueue to Redis, after persistence
    await redis_client.rpush("incident_events", ticket.model_dump_json())

    return Response(status_code=202, content="Incident received successfully")

