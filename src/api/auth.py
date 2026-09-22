import jwt
from fastapi import Header, HTTPException, Depends
from src.api.dependencies import get_settings

def verify_token(authorization: str = Header(None), settings = Depends(get_settings)):
    expected = f"Bearer {settings.webhook_auth_token}"
    if authorization != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")

def decode_bearer_token(authorization: str = Header(None), settings = Depends(get_settings)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized")

    token = authorization.removeprefix("Bearer ")
    try:
        payload = jwt.decode(token, settings.webhook_auth_token, algorithms=["HS256"])
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Unauthorized")

    return payload

def require_operator_role(payload: dict = Depends(decode_bearer_token)):
    if payload.get("role") != "operator":
        raise HTTPException(status_code=403, detail="Operator role required")
    return payload