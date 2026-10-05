"""Operator login: issues a short-lived JWT for dashboard access."""

import hmac
import time
import jwt
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from src.api.dependencies import get_settings
from src.api.schemas import Settings

router = APIRouter()

OPERATOR_TOKEN_TTL_SECONDS = 8 * 60 * 60  # 8 hours


class TokenRequest(BaseModel):
    password: str = Field(..., max_length=256)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = OPERATOR_TOKEN_TTL_SECONDS


@router.post("/api/v1/auth/token", response_model=TokenResponse)
def issue_operator_token(request: TokenRequest, settings: Settings = Depends(get_settings)):
    """
    Issue an operator JWT if the password matches.
    
    - If OPERATOR_PASSWORD or OPERATOR_JWT_SECRET is not set: always 401
    - Uses constant-time comparison on bytes
    - Returns HS256 JWT with role="operator" and 8h expiry
    """
    if not settings.operator_password or not settings.operator_jwt_secret:
        raise HTTPException(status_code=401, detail="Unauthorized")

    if not hmac.compare_digest(request.password.encode(), settings.operator_password.encode()):
        raise HTTPException(status_code=401, detail="Unauthorized")

    now = int(time.time())
    payload = {
        "role": "operator",
        "exp": now + OPERATOR_TOKEN_TTL_SECONDS,
    }
    token = jwt.encode(payload, settings.operator_jwt_secret, algorithm="HS256")
    return TokenResponse(access_token=token)