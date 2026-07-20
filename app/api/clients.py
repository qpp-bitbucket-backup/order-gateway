import secrets
from datetime import datetime,timezone
from typing import Union

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlmodel import Session, select

from app.core.auth_admin import verify_admin_key
from app.core.auth_jwt import require_admin
from app.core.database import get_session
from app.models.client import Client
from app.models.user import User
from app.schemas.client import (
    ClientCreateRequest,
    ClientCreatedResponse,
    ClientResponse,
    ClientsListResponse,
    ClientSummary,
    ClientUpdateRequest,
)

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
        is_active=client.is_active,
        created_at=client.created_at,
        updated_at=client.updated_at,
    )


def _generate_token() -> str:
    return secrets.token_urlsafe(32)


def _generate_secret() -> str:
    return secrets.token_hex(32)


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
    client.updated_at = datetime.utcnow()
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
    if request.is_active is not None:
        client.is_active = request.is_active

    new_secret = None
    if request.rotate_secret:
        new_secret = _generate_secret()
        client.secret = new_secret

    client.updated_at = datetime.utcnow()
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
    client.updated_at = datetime.utcnow()
    session.add(client)
    session.commit()
    session.refresh(client)

    return ClientResponse(client=_to_summary(client))
