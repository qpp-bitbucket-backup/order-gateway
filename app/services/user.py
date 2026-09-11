"""User service for CRUD operations and authentication."""
import logging
from datetime import datetime, timezone
from typing import Optional, List, Tuple

from sqlmodel import Session, select, col
from passlib.context import CryptContext

from app.models.user import User, UserRole
from app.core.security import (
    get_password_hash,
    verify_password,
    create_access_token,
    create_password_reset_token,
    decode_password_reset_token,
)
from app.core.config import settings
from app.services.email import email_service
from app.services.email_templates import (
    password_reset_template_data,
    render_password_reset_email,
)

logger = logging.getLogger(__name__)

pwd_context = CryptContext(schemes=["bcrypt"])


def build_reset_link(base_url: str, token: str) -> str:
    """Build the reset link under the given origin (the API request's host
    in HTTP flows, or the PASSWORD_RESET_URL fallback in script/task flows)."""
    return f"{base_url.rstrip('/')}{settings.PASSWORD_RESET_PATH}?token={token}"


class UserService:
    """Service layer for user management."""

    # ── Authentication ──────────────────────────────────────────

    def authenticate(
        self, session: Session, username_or_email: str, password: str
    ) -> Optional[User]:
        """
        Authenticate a user by username/email + password.
        Returns the user if valid, None otherwise.
        Updates last_login_at and failed_login_count.
        """
        user = session.exec(
            select(User).where(
                (User.username == username_or_email) | (User.email == username_or_email)
            )
        ).first()

        if not user:
            return None

        if not user.is_active:
            logger.warning("User '%s' is disabled", user.username)
            return None

        if not verify_password(password, user.hashed_password):
            user.failed_login_count += 1
            session.add(user)
            session.commit()
            logger.warning(
                "Failed login for user '%s' (attempt %d)",
                user.username,
                user.failed_login_count,
            )
            return None

        # Successful login – reset counters and update last_login_at
        user.failed_login_count = 0
        user.last_login_at = datetime.now(timezone.utc).isoformat()
        session.add(user)
        session.commit()
        session.refresh(user)
        return user

    def create_token_for_user(self, user: User) -> str:
        """Generate a JWT access token for the given user."""
        return create_access_token(
            data={
                "sub": str(user.id),
                "username": user.username,
                "role": user.role.value,
            }
        )

    # ── CRUD ────────────────────────────────────────────────────

    def create_user(
        self,
        session: Session,
        username: str,
        email: str,
        password: str,
        full_name: Optional[str] = None,
        role: UserRole = UserRole.VIEWER,
        store_id: Optional[str] = None,
    ) -> User:
        """Create a new user with hashed password."""
        user = User(
            username=username,
            email=email,
            hashed_password=get_password_hash(password),
            full_name=full_name,
            role=role,
            store_id=store_id,
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        logger.info("User '%s' created (id=%d, role=%s)", user.username, user.id, user.role.value)
        return user

    def get_user_by_id(self, session: Session, user_id: int) -> Optional[User]:
        """Get user by ID."""
        return session.get(User, user_id)

    def get_user_by_username(self, session: Session, username: str) -> Optional[User]:
        """Get user by username."""
        return session.exec(select(User).where(User.username == username)).first()

    def get_user_by_email(self, session: Session, email: str) -> Optional[User]:
        """Get user by email."""
        return session.exec(select(User).where(User.email == email)).first()

    def list_users(
        self,
        session: Session,
        page: int = 1,
        pagesize: int = 20,
        role: Optional[UserRole] = None,
        store_id: Optional[str] = None,
        is_active: Optional[bool] = None,
    ) -> Tuple[List[User], int, int]:
        """
        List users with pagination and optional filters.
        Returns (users, total_count, total_pages).
        """
        query = select(User)

        if role is not None:
            query = query.where(User.role == role)
        if store_id is not None:
            query = query.where(User.store_id == store_id)
        if is_active is not None:
            query = query.where(User.is_active == is_active)

        total_count = len(session.exec(select(User).where(
            *([User.role == role] if role else []),
            *([User.store_id == store_id] if store_id else []),
            *([User.is_active == is_active] if is_active is not None else []),
        )).all())

        total_pages = max((total_count + pagesize - 1) // pagesize, 1)
        offset = (page - 1) * pagesize

        users = session.exec(
            query.order_by(col(User.created_at).desc()).offset(offset).limit(pagesize)
        ).all()

        return users, total_count, total_pages

    def update_user(
        self,
        session: Session,
        user: User,
        email: Optional[str] = None,
        password: Optional[str] = None,
        full_name: Optional[str] = None,
        role: Optional[UserRole] = None,
        store_id: Optional[str] = None,
        is_active: Optional[bool] = None,
    ) -> Tuple[User, List[str]]:
        """
        Update user fields. Returns (user, list_of_changed_fields).
        """
        changes = []

        if email is not None:
            user.email = email
            changes.append("email")
        if password is not None:
            user.hashed_password = get_password_hash(password)
            changes.append("password")
        if full_name is not None:
            user.full_name = full_name
            changes.append("full_name")
        if role is not None:
            user.role = role
            changes.append("role")
        if store_id is not None:
            user.store_id = store_id
            changes.append("store_id")
        if is_active is not None:
            user.is_active = is_active
            changes.append("is_active")

        user.updated_at = datetime.now(timezone.utc)
        session.add(user)
        session.commit()
        session.refresh(user)

        if changes:
            logger.info("User '%s' updated fields: %s", user.username, ", ".join(changes))

        return user, changes

    def forgot_password(
        self, session: Session, email: str, base_url: Optional[str] = None
    ) -> bool:
        """
        Generate a password reset token for the user matching ``email`` and
        send the reset email via SendGrid.

        ``base_url``: scheme+host of the triggering HTTP request — the reset
        link points at ``<base_url>/admin/reset-password`` so it works on
        whatever domain the API was reached through. Falls back to
        settings.PASSWORD_RESET_URL when absent (scripts, background tasks).

        Returns True if the email was sent, False if the user does not exist,
        is inactive, or SendGrid failed. The endpoint is expected to return
        the same response either way so the API does not leak which emails
        are registered.
        """
        user = self.get_user_by_email(session, email)
        if not user or not user.is_active:
            logger.info("Password reset requested for unknown/inactive email")
            return False

        token = create_password_reset_token(user.id)
        reset_link = build_reset_link(
            base_url or settings.PASSWORD_RESET_URL, token
        )
        expire_minutes = settings.PASSWORD_RESET_TOKEN_EXPIRE_MINUTES
        display_name = user.full_name or user.username

        if settings.SENDGRID_RESET_PASSWORD_TEMPLATE_ID:
            sent = email_service.send_templated_email(
                to_email=user.email,
                template_id=settings.SENDGRID_RESET_PASSWORD_TEMPLATE_ID,
                dynamic_template_data=password_reset_template_data(
                    display_name=display_name,
                    reset_link=reset_link,
                    expire_minutes=expire_minutes,
                ),
            )
        else:
            subject, text_content, html_content = render_password_reset_email(
                display_name=display_name,
                reset_link=reset_link,
                expire_minutes=expire_minutes,
            )
            sent = email_service.send_email(
                to_email=user.email,
                subject=subject,
                html_content=html_content,
                text_content=text_content,
            )
        if sent:
            logger.info("Password reset email sent to user '%s'", user.username)
        else:
            logger.error("Failed to send password reset email to user '%s'", user.username)
        return sent

    def reset_password(self, session: Session, token: str, new_password: str) -> bool:
        """
        Reset a user's password using a valid reset token.

        Returns True on success, False if the token is invalid/expired or
        the user no longer exists.
        """
        user_id = decode_password_reset_token(token)
        if not user_id:
            return False

        user = self.get_user_by_id(session, int(user_id))
        if not user or not user.is_active:
            return False

        user.hashed_password = get_password_hash(new_password)
        user.failed_login_count = 0
        user.updated_at = datetime.now(timezone.utc)
        session.add(user)
        session.commit()
        logger.info("Password reset completed for user '%s'", user.username)
        return True

    def change_password(
        self, session: Session, user: User, old_password: str, new_password: str
    ) -> bool:
        """
        Change user password. Returns True on success, False if old password is wrong.
        """
        if not verify_password(old_password, user.hashed_password):
            return False

        user.hashed_password = get_password_hash(new_password)
        user.updated_at = datetime.now(timezone.utc)
        session.add(user)
        session.commit()
        logger.info("Password changed for user '%s'", user.username)
        return True

    def delete_user(self, session: Session, user: User) -> None:
        """Soft-delete a user (set is_active=False)."""
        user.is_active = False
        user.updated_at = datetime.now(timezone.utc)
        session.add(user)
        session.commit()
        logger.info("User '%s' deactivated", user.username)


user_service = UserService()
