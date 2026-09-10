import secrets
from datetime import datetime, timezone, date, timedelta
from typing import Optional, Union

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlmodel import Session, select

from app.core.auth_admin import verify_admin_key
from app.core.auth_jwt import get_current_user, require_admin, resolve_scoped_store_id
from app.core.database import get_session
from app.core.security import verify_password
from app.models.client import Client
from app.models.daily_sales_stat import DailySalesStat
from app.models.user import User
from app.schemas.client import (
    ClientCreateRequest,
    ClientCreatedResponse,
    ClientResponse,
    ClientsListResponse,
    ClientSummary,
    ClientUpdateRequest,
    ClientSecretRevealRequest,
    ClientSecretRevealResponse,
    ClientSecretUpdateRequest,
    ClientStoreKeyRevealRequest,
    ClientStoreKeyRevealResponse,
    ClientStoreKeyUpdateRequest,
    PlatformClientCreateRequest,
    PlatformSalesStatSummary,
    PlatformSalesStatsResponse,
)
from app.schemas.webhook_registration import (
    WebhookRegistration,
    WebhookRegistrationCreateRequest,
    WebhookRegistrationResponse,
    WebhookRegistrationsListResponse,
    WebhookRegistrationUpdateRequest,
)
from app.services import qpmn_webhook
from app.services.order import order_service

router = APIRouter(
    prefix="/api/client",
    tags=["Clients"],
    dependencies=[Depends(verify_admin_key)],
)


def _to_summary(client: Client) -> ClientSummary:
    return ClientSummary(
        id=client.id,
        name=client.name,
        store_id=client.store_id,
        token=client.token,
        description=client.description,
        cooling_off_seconds=client.cooling_off_seconds or 0,
        is_active=client.is_active,
        created_at=client.created_at,
        updated_at=client.updated_at,
    )


def _generate_token() -> str:
    """Generate a 12-char random hex string (48 bits of entropy)."""
    return secrets.token_hex(6)


def _generate_secret() -> str:
    """Generate a 32-char random hex string (128 bits of entropy)."""
    return secrets.token_hex(16)


@router.post("", response_model=ClientCreatedResponse, status_code=status.HTTP_201_CREATED)
def create_client(
    request: ClientCreateRequest,
    session: Session = Depends(get_session),
):
    """Create a new API client with auto-generated token and secret."""
    existing = session.exec(select(Client).where(Client.store_id == request.store_id)).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Client with store_id '{request.store_id}' already exists",
        )

    token = _generate_token()
    secret = _generate_secret()

    client = Client(
        name=request.name,
        store_id=request.store_id,
        store_key=request.store_key,
        token=token,
        secret=secret,
        description=request.description,
        cooling_off_seconds=request.cooling_off_seconds or 0,
    )
    session.add(client)
    session.commit()
    session.refresh(client)

    return ClientCreatedResponse(
        client=_to_summary(client),
        secret=secret,
    )


@router.get("", response_model=ClientsListResponse)
def list_clients(
    page: int = Query(1, ge=1, description="Page number"),
    pagesize: int = Query(20, ge=1, le=100, description="Items per page"),
    session: Session = Depends(get_session),
):
    """List all API clients."""
    offset = (page - 1) * pagesize
    clients = session.exec(
        select(Client).order_by(Client.created_at.desc()).offset(offset).limit(pagesize)
    ).all()
    total_count = len(session.exec(select(Client)).all())
    total_pages = max((total_count + pagesize - 1) // pagesize, 1)

    return ClientsListResponse(
        count=total_count,
        page=page,
        pages=total_pages,
        data=[_to_summary(client) for client in clients],
    )


@router.get("/{client_id}", response_model=ClientResponse)
def get_client(
    client_id: int,
    session: Session = Depends(get_session),
):
    """Get a single API client by ID."""
    client = session.get(Client, client_id)
    if not client:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Client with ID '{client_id}' not found",
        )

    return ClientResponse(client=_to_summary(client))


@router.put("/{client_id}", response_model=Union[ClientCreatedResponse, ClientResponse])
def update_client(
    client_id: int,
    request: ClientUpdateRequest,
    session: Session = Depends(get_session),
):
    """Update an API client. Set rotate_secret=true to generate a new secret."""
    client = session.get(Client, client_id)
    if not client:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Client with ID '{client_id}' not found",
        )

    if request.name is not None:
        client.name = request.name
    if request.store_id is not None:
        existing = session.exec(
            select(Client).where(Client.store_id == request.store_id, Client.id != client_id)
        ).first()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Client with store_id '{request.store_id}' already exists",
            )
        client.store_id = request.store_id
    if request.store_key is not None:
        client.store_key = request.store_key
    if request.description is not None:
        client.description = request.description
    if request.cooling_off_seconds is not None:
        client.cooling_off_seconds = request.cooling_off_seconds
    if request.is_active is not None:
        client.is_active = request.is_active

    new_secret = None
    if request.rotate_secret:
        new_secret = _generate_secret()
        client.secret = new_secret

    client.updated_at = datetime.now(timezone.utc)
    session.add(client)
    session.commit()
    session.refresh(client)

    if request.rotate_secret:
        return ClientCreatedResponse(
            client=_to_summary(client),
            secret=new_secret,
        )

    return ClientResponse(client=_to_summary(client))


@router.delete("/{client_id}", response_model=ClientResponse)
def deactivate_client(
    client_id: int,
    session: Session = Depends(get_session),
):
    """Deactivate an API client (soft delete)."""
    client = session.get(Client, client_id)
    if not client:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Client with ID '{client_id}' not found",
        )

    client.is_active = False
    client.updated_at = datetime.now(timezone.utc)
    session.add(client)
    session.commit()
    session.refresh(client)

    return ClientResponse(client=_to_summary(client))


# ---------------------------------------------------------------------------
# Platform JWT router – requires JWT Bearer token (admin only)
# ---------------------------------------------------------------------------

jwt_router = APIRouter(
    prefix="/api/platform",
    tags=["Platform"],
)


@jwt_router.post("/clients", response_model=ClientCreatedResponse, status_code=status.HTTP_201_CREATED)
def platform_create_client(
    request: PlatformClientCreateRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_admin),
):
    """Create a new API client with auto-generated token and secret (JWT admin only).

    All fields (name, store_id, store_key, description) are required. The
    store_id + store_key pair is verified against the QPMN store API before
    anything is persisted — a failed verification means the store does not
    exist or the authorization is invalid (HTTP 400). The OneFlow token and
    secret are generated server-side; the secret is shown only once in the
    response.
    """
    existing = session.exec(select(Client).where(Client.store_id == request.store_id)).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Client with store_id '{request.store_id}' already exists",
        )

    # Verify the store exists on QPMN and the key authorizes (no Sentry
    # alert on failure — an expected user error, see verify_store_credentials).
    verified, reason = order_service.verify_store_credentials(
        request.store_id, request.store_key
    )
    if not verified:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Store verification failed for store_id '{request.store_id}': "
                f"{reason} (store does not exist or authorization failed)"
            ),
        )

    token = _generate_token()
    secret = _generate_secret()

    client = Client(
        name=request.name,
        store_id=request.store_id,
        store_key=request.store_key,
        token=token,
        secret=secret,
        description=request.description,
        cooling_off_seconds=request.cooling_off_seconds or 0,
    )
    session.add(client)
    session.commit()
    session.refresh(client)

    return ClientCreatedResponse(
        client=_to_summary(client),
        secret=secret,
    )


def _confirm_password_or_403(current_user: User, password: str) -> None:
    """Sensitive-operation confirmation: re-check the user's login password."""
    if not verify_password(password, current_user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Password confirmation failed",
        )


def _get_client_by_store_id_or_404(session: Session, store_id: str) -> Client:
    client = session.exec(select(Client).where(Client.store_id == store_id)).first()
    if not client:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Client with store_id '{store_id}' not found",
        )
    return client


@jwt_router.post("/clients/secret", response_model=ClientSecretRevealResponse)
def platform_reveal_client_secret(
    request: ClientSecretRevealRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_admin),
):
    """Reveal a client's secret by store_id (JWT admin + password confirmation).

    The login password must be re-supplied and verified against the current
    user before the secret is disclosed — a JWT alone is not enough for this
    sensitive operation. Password is checked before the store lookup so a
    wrong password never leaks whether the store exists.
    """
    _confirm_password_or_403(current_user, request.password)
    client = _get_client_by_store_id_or_404(session, request.store_id)

    return ClientSecretRevealResponse(
        success=True,
        store_id=client.store_id,
        secret=client.secret,
    )


@jwt_router.put("/clients/secret", response_model=ClientResponse)
def platform_update_client_secret(
    request: ClientSecretUpdateRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_admin),
):
    """Update a client's secret by store_id (JWT admin only).

    No password re-confirmation for this operation — only the reveal (POST)
    endpoint keeps it. The new secret must be at least 32 characters long
    (enforced both at the schema layer and again here before saving, so the
    rule cannot be bypassed by schema changes alone).
    """
    client = _get_client_by_store_id_or_404(session, request.store_id)

    if len(request.secret) < 32:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="New secret must be at least 32 characters long",
        )

    client.secret = request.secret
    client.updated_at = datetime.now(timezone.utc)
    session.add(client)
    session.commit()
    session.refresh(client)

    return ClientResponse(client=_to_summary(client))


@jwt_router.post("/clients/store_key", response_model=ClientStoreKeyRevealResponse)
def platform_reveal_client_store_key(
    request: ClientStoreKeyRevealRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_admin),
):
    """Reveal a client's store key by store_id (JWT admin only)."""
    client = _get_client_by_store_id_or_404(session, request.store_id)

    return ClientStoreKeyRevealResponse(
        success=True,
        store_id=client.store_id,
        store_key=client.store_key,
    )


@jwt_router.put("/clients/store_key", response_model=ClientResponse)
def platform_update_client_store_key(
    request: ClientStoreKeyUpdateRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_admin),
):
    """Update a client's store key by store_id (JWT admin only).

    The new store_id + store_key pair is verified against the QPMN store API
    (order_service.verify_store_credentials) before anything is persisted —
    a failed verification means the store does not exist or the key is not
    authorized (HTTP 400), and the stored key is left untouched. Reuses the
    same verification semantics as client creation.
    """
    client = _get_client_by_store_id_or_404(session, request.store_id)
    print(request)
    verified, reason = order_service.verify_store_credentials(
        request.store_id, request.store_key
    )
    print(verified)
    if not verified:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Store verification failed for store_id '{request.store_id}': "
                f"{reason} (store does not exist or authorization failed)"
            ),
        )

    client.store_key = request.store_key
    client.updated_at = datetime.now(timezone.utc)
    session.add(client)
    session.commit()
    session.refresh(client)

    return ClientResponse(client=_to_summary(client))


@jwt_router.get("/sales-stats", response_model=PlatformSalesStatsResponse)
def platform_get_sales_stats(
    start_date: Optional[date] = Query(None, description="Range start (YYYY-MM-DD, inclusive); defaults to 29 days before end_date"),
    end_date: Optional[date] = Query(None, description="Range end (YYYY-MM-DD, inclusive); defaults to today"),
    store_id: Optional[str] = Query(None, description="Filter by store (meaningful for ADMIN; scoped users are always limited to their own store)"),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Sales statistics per store for frontend reports (JWT).

    One row per store per day from ``daily_sales_stats`` over the requested
    date range (inclusive), joined with the client (store) display name,
    ordered by ``statDate`` ascending (then store name). ADMIN sees every
    store (optionally narrowed by ``store_id``); EDITOR/VIEWER only ever see
    their own store. Deactivated clients keep their historical rows.
    """
    # Outside the try block so the 403 for out-of-scope stores is not
    # swallowed into a 500 by the generic exception handler below.
    scoped_store_id = resolve_scoped_store_id(current_user, store_id)
    try:
        end = end_date or datetime.now(timezone.utc).date()
        start = start_date or (end - timedelta(days=29))
        if start > end:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="start_date must be on or before end_date",
            )

        query = (
            select(
                DailySalesStat.stat_date,
                Client.id,
                Client.name,
                Client.store_id,
                DailySalesStat.currency,
                DailySalesStat.orders_requested,
                DailySalesStat.orders_submitted,
                DailySalesStat.line_items_count,
                DailySalesStat.line_items_quantity,
                DailySalesStat.total_amount,
            )
            .join(Client, DailySalesStat.client_id == Client.id)
            .where(
                DailySalesStat.stat_date >= start,
                DailySalesStat.stat_date <= end,
            )
        )
        if scoped_store_id:
            query = query.where(DailySalesStat.store_id == scoped_store_id)
        query = query.order_by(
            DailySalesStat.stat_date.asc(), Client.name.asc(), Client.store_id.asc(),
        )

        rows = session.exec(query).all()
        data = [
            PlatformSalesStatSummary(
                statDate=r[0],
                clientId=r[1],
                storeName=r[2],
                storeId=r[3],
                currency=r[4],
                ordersRequested=r[5] or 0,
                ordersSubmitted=r[6] or 0,
                lineItemsCount=r[7] or 0,
                lineItemsQuantity=r[8] or 0,
                totalAmount=round(r[9] or 0.0, 2),
            )
            for r in rows
        ]

        return PlatformSalesStatsResponse(
            success=True,
            count=len(data),
            startDate=start,
            endDate=end,
            data=data,
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve sales statistics: {str(e)}",
        )


@jwt_router.get("/clients", response_model=ClientsListResponse)
def platform_list_clients(
    page: int = Query(1, ge=1, description="Page number"),
    pagesize: int = Query(20, ge=1, le=100, description="Items per page"),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_admin),
):
    """List all API clients (JWT admin only)."""
    offset = (page - 1) * pagesize
    clients = session.exec(
        select(Client).order_by(Client.created_at.desc()).offset(offset).limit(pagesize)
    ).all()
    total_count = len(session.exec(select(Client)).all())
    total_pages = max((total_count + pagesize - 1) // pagesize, 1)

    return ClientsListResponse(
        count=total_count,
        page=page,
        pages=total_pages,
        data=[_to_summary(client) for client in clients],
    )


@jwt_router.get("/clients/{client_id}", response_model=ClientResponse)
def platform_get_client(
    client_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_admin),
):
    """Get a single API client by ID (JWT admin only)."""
    client = session.get(Client, client_id)
    if not client:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Client with ID '{client_id}' not found",
        )

    return ClientResponse(client=_to_summary(client))


@jwt_router.put("/clients/{client_id}", response_model=Union[ClientCreatedResponse, ClientResponse])
def platform_update_client(
    client_id: int,
    request: ClientUpdateRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_admin),
):
    """Update an API client (JWT admin only). Set rotate_secret=true to generate a new secret."""
    client = session.get(Client, client_id)
    if not client:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Client with ID '{client_id}' not found",
        )

    if request.name is not None:
        client.name = request.name
    if request.store_id is not None:
        existing = session.exec(
            select(Client).where(Client.store_id == request.store_id, Client.id != client_id)
        ).first()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Client with store_id '{request.store_id}' already exists",
            )
        client.store_id = request.store_id
    if request.store_key is not None:
        client.store_key = request.store_key
    if request.description is not None:
        client.description = request.description
    if request.cooling_off_seconds is not None:
        client.cooling_off_seconds = request.cooling_off_seconds
    if request.is_active is not None:
        client.is_active = request.is_active

    new_secret = None
    if request.rotate_secret:
        new_secret = _generate_secret()
        client.secret = new_secret

    client.updated_at = datetime.now(timezone.utc)
    session.add(client)
    session.commit()
    session.refresh(client)

    if request.rotate_secret:
        return ClientCreatedResponse(
            client=_to_summary(client),
            secret=new_secret,
        )

    return ClientResponse(client=_to_summary(client))


@jwt_router.delete("/clients/{client_id}", response_model=ClientResponse)
def platform_deactivate_client(
    client_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_admin),
):
    """Deactivate an API client (soft delete) (JWT admin only)."""
    client = session.get(Client, client_id)
    if not client:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Client with ID '{client_id}' not found",
        )

    client.is_active = False
    client.updated_at = datetime.now(timezone.utc)
    session.add(client)
    session.commit()
    session.refresh(client)

    return ClientResponse(client=_to_summary(client))


# ---------------------------------------------------------------------------
# Webhook registration proxy (QPMN §4) — not persisted locally, QPMN is the
# source of truth per Ivan (2026-08-11).
# ---------------------------------------------------------------------------

def _get_webhook_client_or_404(client_id: int, session: Session) -> Client:
    client = session.get(Client, client_id)
    if not client:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Client with ID '{client_id}' not found",
        )
    if not client.store_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Client '{client_id}' has no store_key configured",
        )
    return client


def _qpmn_error_to_http(e: qpmn_webhook.QpmnApiError) -> HTTPException:
    status_code = e.status_code if e.status_code in (400, 401, 404, 422) else status.HTTP_502_BAD_GATEWAY
    return HTTPException(status_code=status_code, detail=e.message)


@jwt_router.post("/clients/{client_id}/webhooks", response_model=WebhookRegistrationResponse, status_code=status.HTTP_201_CREATED)
def platform_create_webhook(
    client_id: int,
    request: WebhookRegistrationCreateRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_admin),
):
    """Register a QPMN webhook for this client's store (§4.1, JWT admin only)."""
    client = _get_webhook_client_or_404(client_id, session)
    try:
        data = qpmn_webhook.create_webhook(client.store_key, request.name, request.url, request.eventTypes, request.enabled)
    except qpmn_webhook.QpmnApiError as e:
        raise _qpmn_error_to_http(e)
    return WebhookRegistrationResponse(webhook=WebhookRegistration(**data))


@jwt_router.get("/clients/{client_id}/webhooks", response_model=WebhookRegistrationsListResponse)
def platform_list_webhooks(
    client_id: int,
    page: int = Query(1, ge=1, description="Page number"),
    pagesize: int = Query(20, ge=1, le=100, description="Items per page"),
    enabled: Optional[bool] = Query(None, description="Filter by enabled status"),
    name: Optional[str] = Query(None, description="Filter by name"),
    session: Session = Depends(get_session),
    current_user: User = Depends(require_admin),
):
    """List QPMN webhooks registered for this client's store (§4.2, JWT admin only)."""
    client = _get_webhook_client_or_404(client_id, session)
    try:
        data = qpmn_webhook.list_webhooks(client.store_key, page=page, size=pagesize, enabled=enabled, name=name)
    except qpmn_webhook.QpmnApiError as e:
        raise _qpmn_error_to_http(e)
    content = data.get("content", [])
    return WebhookRegistrationsListResponse(
        count=data.get("totalCount", len(content)),
        page=data.get("pageNumber", page),
        pages=data.get("totalPages", 1),
        data=[WebhookRegistration(**item) for item in content],
    )


@jwt_router.get("/clients/{client_id}/webhooks/{webhook_id}", response_model=WebhookRegistrationResponse)
def platform_get_webhook(
    client_id: int,
    webhook_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_admin),
):
    """Get a single QPMN webhook by id for this client's store (§4.3, JWT admin only)."""
    client = _get_webhook_client_or_404(client_id, session)
    try:
        data = qpmn_webhook.get_webhook(client.store_key, webhook_id)
    except qpmn_webhook.QpmnApiError as e:
        raise _qpmn_error_to_http(e)
    return WebhookRegistrationResponse(webhook=WebhookRegistration(**data))


@jwt_router.put("/clients/{client_id}/webhooks/{webhook_id}", response_model=WebhookRegistrationResponse)
def platform_update_webhook(
    client_id: int,
    webhook_id: int,
    request: WebhookRegistrationUpdateRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_admin),
):
    """Update a QPMN webhook for this client's store (§4.4, JWT admin only)."""
    client = _get_webhook_client_or_404(client_id, session)
    try:
        data = qpmn_webhook.update_webhook(client.store_key, webhook_id, request.name, request.url, request.eventTypes, request.enabled)
    except qpmn_webhook.QpmnApiError as e:
        raise _qpmn_error_to_http(e)
    return WebhookRegistrationResponse(webhook=WebhookRegistration(**data))


@jwt_router.delete("/clients/{client_id}/webhooks/{webhook_id}", response_model=WebhookRegistrationResponse)
def platform_delete_webhook(
    client_id: int,
    webhook_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_admin),
):
    """Delete a QPMN webhook for this client's store (§4.5, JWT admin only)."""
    client = _get_webhook_client_or_404(client_id, session)
    try:
        data = qpmn_webhook.delete_webhook(client.store_key, webhook_id)
    except qpmn_webhook.QpmnApiError as e:
        raise _qpmn_error_to_http(e)
    return WebhookRegistrationResponse(webhook=WebhookRegistration(**data))
