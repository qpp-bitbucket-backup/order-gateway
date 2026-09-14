"""User management API endpoints with JWT authentication."""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlmodel import Session

from app.core.database import get_session
from app.core.auth_jwt import get_current_user, require_admin, require_editor_or_above
from app.models.user import User, UserRole
from app.schemas.user import (
    UserCreate,
    UserUpdate,
    UserResponse,
    LoginRequest,
    LoginResponse,
    ChangePasswordRequest,
    ForgotPasswordRequest,
    ResetPasswordRequest,
)
from app.services.user import user_service
from app.services.email import email_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/platform", tags=["Platform"])


# ── Auth (public) ───────────────────────────────────────────────

@router.post("/users/login", response_model=LoginResponse)
def login(
    request: LoginRequest,
    session: Session = Depends(get_session),
):
    """
    用戶登入 - 使用 username/email + password 獲取 JWT token。
    """
    user = user_service.authenticate(session, request.username, request.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username/email or password",
        )

    token = user_service.create_token_for_user(user)
    return LoginResponse(
        access_token=token,
        user=UserResponse(
            id=user.id,
            username=user.username,
            email=user.email,
            full_name=user.full_name,
            role=user.role,
            store_id=user.store_id,
            is_active=user.is_active,
            last_login_at=user.last_login_at,
            created_at=user.created_at,
            updated_at=user.updated_at,
        ),
    )


@router.get("/users/me", response_model=UserResponse)
def get_current_user_profile(
    current_user: User = Depends(get_current_user),
):
    """
    獲取當前登入用戶信息 - 需要 JWT token。
    """
    return UserResponse(
        id=current_user.id,
        username=current_user.username,
        email=current_user.email,
        full_name=current_user.full_name,
        role=current_user.role,
        store_id=current_user.store_id,
        is_active=current_user.is_active,
        last_login_at=current_user.last_login_at,
        created_at=current_user.created_at,
        updated_at=current_user.updated_at,
    )


@router.put("/users/me/password")
def change_password(
    request: ChangePasswordRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """
    修改當前用戶密碼 - 需要 JWT token。
    """
    success = user_service.change_password(
        session, current_user, request.old_password, request.new_password
    )
    if not success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect",
        )
    return {"message": "Password changed successfully"}


@router.post("/users/forgot-password")
def forgot_password(
    request: ForgotPasswordRequest,
    http_request: Request,
    session: Session = Depends(get_session),
):
    """
    忘記密碼 - 發送重置密碼郵件（SendGrid）。

    重置鏈接基於當前請求的域名：/<host>/admin/reset-password?token=...。
    無論郵箱是否存在都返回相同的成功響應，避免洩露已註冊郵箱。
    """
    if not email_service.is_configured():
        logger.error("Forgot password requested but SENDGRID_API_KEY is not configured")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Email service is not configured",
        )

    # e.g. called via https://api.example.com -> link under that origin.
    # Behind a reverse proxy, run uvicorn with --proxy-headers so scheme/host
    # follow X-Forwarded-Proto / X-Forwarded-For.
    base_url = str(http_request.base_url).rstrip("/")
    user_service.forgot_password(session, request.email, base_url=base_url)
    return {
        "message": "If the email address is registered, a password reset link has been sent"
    }


@router.post("/users/reset-password")
def reset_password(
    request: ResetPasswordRequest,
    session: Session = Depends(get_session),
):
    """
    重置密碼 - 使用重置郵件中的 token 設置新密碼。
    """
    success = user_service.reset_password(session, request.token, request.new_password)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired password reset token",
        )
    return {"message": "Password has been reset successfully"}


# ── User CRUD (admin only) ──────────────────────────────────────

@router.post("/users", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def create_user(
    request: UserCreate,
    session: Session = Depends(get_session),
    admin: User = Depends(require_admin),
):
    """
    創建新用戶 - 僅限管理員。
    """
    # Check duplicate username
    existing = user_service.get_user_by_username(session, request.username)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Username '{request.username}' already exists",
        )

    # Check duplicate email
    existing = user_service.get_user_by_email(session, request.email)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Email '{request.email}' already exists",
        )

    user = user_service.create_user(
        session,
        username=request.username,
        email=request.email,
        password=request.password,
        full_name=request.full_name,
        role=request.role,
        store_id=request.store_id,
    )

    return UserResponse(
        id=user.id,
        username=user.username,
        email=user.email,
        full_name=user.full_name,
        role=user.role,
        store_id=user.store_id,
        is_active=user.is_active,
        last_login_at=user.last_login_at,
        created_at=user.created_at,
        updated_at=user.updated_at,
    )


@router.get("/users", response_model=dict)
def list_users(
    page: int = Query(1, ge=1, description="Page number"),
    pagesize: int = Query(20, ge=1, le=100, description="Items per page"),
    role: Optional[UserRole] = Query(None, description="Filter by role"),
    store_id: Optional[str] = Query(None, description="Filter by store_id"),
    is_active: Optional[bool] = Query(None, description="Filter by active status"),
    session: Session = Depends(get_session),
    admin: User = Depends(require_admin),
):
    """
    獲取用戶列表（分頁）- 僅限管理員。
    """
    users, total_count, total_pages = user_service.list_users(
        session,
        page=page,
        pagesize=pagesize,
        role=role,
        store_id=store_id,
        is_active=is_active,
    )

    return {
        "count": total_count,
        "page": page,
        "pages": total_pages,
        "data": [
            UserResponse(
                id=u.id,
                username=u.username,
                email=u.email,
                full_name=u.full_name,
                role=u.role,
                store_id=u.store_id,
                is_active=u.is_active,
                last_login_at=u.last_login_at,
                created_at=u.created_at,
                updated_at=u.updated_at,
            )
            for u in users
        ],
    }


@router.get("/users/{user_id}", response_model=UserResponse)
def get_user(
    user_id: int,
    session: Session = Depends(get_session),
    admin: User = Depends(require_admin),
):
    """
    獲取指定用戶信息 - 僅限管理員。
    """
    user = user_service.get_user_by_id(session, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with ID '{user_id}' not found",
        )
    return UserResponse(
        id=user.id,
        username=user.username,
        email=user.email,
        full_name=user.full_name,
        role=user.role,
        store_id=user.store_id,
        is_active=user.is_active,
        last_login_at=user.last_login_at,
        created_at=user.created_at,
        updated_at=user.updated_at,
    )


@router.put("/users/{user_id}", response_model=UserResponse)
def update_user(
    user_id: int,
    request: UserUpdate,
    session: Session = Depends(get_session),
    admin: User = Depends(require_admin),
):
    """
    更新用戶信息 - 僅限管理員。
    """
    user = user_service.get_user_by_id(session, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with ID '{user_id}' not found",
        )

    # Check email uniqueness if updating email
    if request.email is not None:
        existing = user_service.get_user_by_email(session, request.email)
        if existing and existing.id != user_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Email '{request.email}' already exists",
            )

    user, changes = user_service.update_user(
        session,
        user,
        email=request.email,
        password=request.password,
        full_name=request.full_name,
        role=request.role,
        store_id=request.store_id,
        is_active=request.is_active,
    )

    if not changes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one field must be provided for update",
        )

    return UserResponse(
        id=user.id,
        username=user.username,
        email=user.email,
        full_name=user.full_name,
        role=user.role,
        store_id=user.store_id,
        is_active=user.is_active,
        last_login_at=user.last_login_at,
        created_at=user.created_at,
        updated_at=user.updated_at,
    )


@router.delete("/users/{user_id}")
def delete_user(
    user_id: int,
    session: Session = Depends(get_session),
    admin: User = Depends(require_admin),
):
    """
    停用用戶（軟刪除）- 僅限管理員。
    """
    user = user_service.get_user_by_id(session, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User with ID '{user_id}' not found",
        )

    if user.id == admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot deactivate your own account",
        )

    user_service.delete_user(session, user)
    return {"message": f"User '{user.username}' has been deactivated"}
