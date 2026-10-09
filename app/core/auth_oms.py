"""
OMS inbound API authentication.

Same signed-header mechanism as the VFS-facing OneFlow auth
(``app.core.auth_oneflow``) — identical headers, identical
``{METHOD} {PATH} {TIMESTAMP}`` string-to-sign, identical ±300s time
window — but verified against the per-client ``oms_token`` / ``oms_secret``
columns of the ``clients`` table (managed via the platform API). A client
without OMS credentials cannot authenticate against the OMS endpoints.

Required headers (same names as OneFlow, so OMS's signing code can be a
copy of VFS's with different credentials):
- x-oneflow-authorization: {oms_token}:{signature}
- x-oneflow-date: ISO 8601 timestamp used in signing
- x-oneflow-algorithm: SHA256
"""
import hmac
from typing import Optional

from fastapi import Depends, HTTPException, Request, Security, status
from sqlmodel import Session, select

from app.core.auth_oneflow import (
    SUPPORTED_ALGORITHM,
    _verify_timestamp,
    generate_signature,
    oneflow_algorithm_header,
    oneflow_authorization_header,
    oneflow_date_header,
)
from app.core.database import get_session
from app.core.rate_limit import enforce_client_rate_limit
from app.models.client import Client


def verify_oms_auth(
    request: Request,
    x_oneflow_authorization: Optional[str] = Security(oneflow_authorization_header),
    x_oneflow_date: Optional[str] = Security(oneflow_date_header),
    x_oneflow_algorithm: Optional[str] = Security(oneflow_algorithm_header),
    session: Session = Depends(get_session),
) -> Client:
    """
    Verify OMS signed request headers against the ``oms_token`` /
    ``oms_secret`` pair stored on the matching ``clients`` row.

    Returns the authenticated ``Client`` on success. Error responses mirror
    ``verify_oneflow_auth`` (401 for missing/expired headers, 403 for a bad
    token or signature) so OMS sees the same failure vocabulary as VFS.
    A presented token that matches no active client with a configured
    ``oms_secret`` is rejected as INVALID_TOKEN.

    The client's per-second rate limit is enforced after the signature has
    been verified, in a separate ``oms`` namespace so OMS traffic does not
    consume the client's VFS-facing (OneFlow) budget.
    """
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

    client = session.exec(
        select(Client).where(Client.oms_token == token)
    ).first()
    if client is None or not client.oms_secret or not client.is_active:
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

    expected_signature = generate_signature(
        client.oms_secret,
        request.method,
        request.url.path,
        x_oneflow_date,
    )
    if not hmac.compare_digest(provided_signature, expected_signature):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid signature",
            headers={"X-Error-Code": "INVALID_SIGNATURE"},
        )

    enforce_client_rate_limit(client, scope="oms", token=token)
    return client
