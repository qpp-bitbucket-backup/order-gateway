"""
HP Site Flow OneFlow API Authentication

Validates requests using signed headers:
- x-oneflow-authorization: Token:Signature
- x-oneflow-date: ISO 8601 timestamp used in signing
- x-oneflow-algorithm: SHA256
"""
from datetime import datetime,timezone, timezone
from typing import Optional
import hashlib
import hmac

from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader
from sqlmodel import Session, select

from app.core.config import settings
from app.core.database import get_session
from app.models.client import Client

SUPPORTED_ALGORITHM = "SHA256"
MAX_TIMESTAMP_AGE_SECONDS = 300

oneflow_authorization_header = APIKeyHeader(
    name="x-oneflow-authorization",
    scheme_name="OneFlowAuthorization",
    description=(
        "Token:Signature. Sign `{METHOD} {PATH} {TIMESTAMP}` with HMAC-SHA256 using your Secret."
    ),
    auto_error=False,
)
oneflow_date_header = APIKeyHeader(
    name="x-oneflow-date",
    scheme_name="OneFlowDate",
    description="ISO 8601 timestamp used when signing (e.g. 2026-01-01T12:00:00.000Z).",
    auto_error=False,
)
oneflow_algorithm_header = APIKeyHeader(
    name="x-oneflow-algorithm",
    scheme_name="OneFlowAlgorithm",
    description="Signing algorithm. Must be SHA256.",
    auto_error=False,
)


def build_string_to_sign(method: str, path: str, timestamp: str) -> str:
    """Build the string to sign: METHOD PATH TIMESTAMP."""
    return f"{method.upper()} {path} {timestamp}"


def generate_signature(secret: str, method: str, path: str, timestamp: str) -> str:
    """Generate HMAC-SHA256 signature for a request."""
    string_to_sign = build_string_to_sign(method, path, timestamp)
    return hmac.new(
        secret.encode("utf-8"),
        string_to_sign.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def build_auth_headers(
    token: str,
    secret: str,
    method: str,
    path: str,
    timestamp: Optional[str] = None,
) -> dict[str, str]:
    """Build OneFlow authentication headers for outbound requests."""
    if timestamp is None:
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    signature = generate_signature(secret, method, path, timestamp)
    return {
        "x-oneflow-authorization": f"{token}:{signature}",
        "x-oneflow-date": timestamp,
        "x-oneflow-algorithm": SUPPORTED_ALGORITHM,
    }


def _parse_iso_timestamp(timestamp: str) -> Optional[datetime]:
    try:
        normalized = timestamp.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized)
    except (ValueError, TypeError):
        return None


def _verify_timestamp(timestamp: str, max_age: int = MAX_TIMESTAMP_AGE_SECONDS) -> bool:
    request_time = _parse_iso_timestamp(timestamp)
    if request_time is None:
        return False

    if request_time.tzinfo is None:
        request_time = request_time.replace(tzinfo=timezone.utc)

    current_time = datetime.now(timezone.utc)
    return abs((current_time - request_time).total_seconds()) <= max_age


def _get_client_by_token(session: Session, token: str) -> Optional[Client]:
    """Look up an active client by token."""
    client = session.exec(
        select(Client).where(Client.token == token, Client.is_active == True)
    ).first()
    if client:
        return client

    # Fallback to bootstrap credentials (no store_id)
    if hmac.compare_digest(token, settings.ONEFLOW_TOKEN):
        # Return a mock client for bootstrap credentials
        return None

    return None


def _get_client_secret(session: Session, token: str) -> Optional[str]:
    """Look up an active client's secret by token."""
    client = session.exec(
        select(Client).where(Client.token == token, Client.is_active == True)
    ).first()
    if client:
        return client.secret

    if hmac.compare_digest(token, settings.ONEFLOW_TOKEN):
        return settings.ONEFLOW_SECRET

    return None


async def verify_oneflow_auth(
    request: Request,
    session: Session = Depends(get_session),
    x_oneflow_authorization: Optional[str] = Security(oneflow_authorization_header),
    x_oneflow_date: Optional[str] = Security(oneflow_date_header),
    x_oneflow_algorithm: Optional[str] = Security(oneflow_algorithm_header),
) -> str:
    """
    Verify OneFlow signed request headers.
    Returns the authenticated token on success.
    """
    print(f"x_oneflow_authorization: {x_oneflow_authorization}")
    print(f"x_oneflow_date: {x_oneflow_date}")
    print(f"x_oneflow_algorithm: {x_oneflow_algorithm}")
    if not x_oneflow_authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="x-oneflow-authorization header is required",
            headers={"X-Error-Code": "MISSING_AUTHORIZATION"},
        )

    if not x_oneflow_date:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="x-oneflow-date header is required",
            headers={"X-Error-Code": "MISSING_DATE"},
        )

    if not x_oneflow_algorithm:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="x-oneflow-algorithm header is required",
            headers={"X-Error-Code": "MISSING_ALGORITHM"},
        )

    if x_oneflow_algorithm.upper() != SUPPORTED_ALGORITHM:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Unsupported algorithm. Expected {SUPPORTED_ALGORITHM}",
            headers={"X-Error-Code": "UNSUPPORTED_ALGORITHM"},
        )

    if ":" not in x_oneflow_authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid x-oneflow-authorization format. Expected Token:Signature",
            headers={"X-Error-Code": "INVALID_AUTHORIZATION_FORMAT"},
        )

    token, provided_signature = x_oneflow_authorization.split(":", 1)
    if not token or not provided_signature:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid x-oneflow-authorization format. Expected Token:Signature",
            headers={"X-Error-Code": "INVALID_AUTHORIZATION_FORMAT"},
        )

    client_secret = _get_client_secret(session, token)
    if client_secret is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid token",
            headers={"X-Error-Code": "INVALID_TOKEN"},
        )

    if not _verify_timestamp(x_oneflow_date):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Request timestamp is invalid or expired",
            headers={"X-Error-Code": "EXPIRED_TIMESTAMP"},
        )
    print(f"client_secret: {client_secret}")
    print(f"request.method: {request.method}")
    print(f"request.url.path: {request.url.path}")
    print(f"x_oneflow_date: {x_oneflow_date}")
    expected_signature = generate_signature(
        client_secret,
        request.method,
        request.url.path,
        x_oneflow_date,
    )
    print(f"expected_signature: {expected_signature}")
    print(f"provided_signature: {provided_signature}")
    if not hmac.compare_digest(provided_signature, expected_signature):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid signature",
            headers={"X-Error-Code": "INVALID_SIGNATURE"},
        )

    return token


async def get_client_store_id(
    request: Request,
    session: Session = Depends(get_session),
    x_oneflow_authorization: Optional[str] = Security(oneflow_authorization_header),
    x_oneflow_date: Optional[str] = Security(oneflow_date_header),
    x_oneflow_algorithm: Optional[str] = Security(oneflow_algorithm_header),
) -> Optional[str]:
    """
    Verify OneFlow auth and return the client's store_id.
    Returns None if using bootstrap credentials.
    """
    # First verify the authentication
    token = await verify_oneflow_auth(
        request=request,
        session=session,
        x_oneflow_authorization=x_oneflow_authorization,
        x_oneflow_date=x_oneflow_date,
        x_oneflow_algorithm=x_oneflow_algorithm,
    )
    
    # Get client by token
    client = _get_client_by_token(session, token)
    
    if client:
        return client.store_id
    
    # Bootstrap credentials have no store_id
    return None
