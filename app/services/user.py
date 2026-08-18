"""User service for CRUD operations and authentication."""
import logging
from datetime import datetime, timezone
from typing import Optional, List, Tuple

from sqlmodel import Session, select, col
from passlib.context import CryptContext

from app.models.user import User, UserRole
from app.core.security import get_password_hash, verify_password, create_access_token
from app.core.config import settings

logger = logging.getLogger(__name__)

pwd_context = CryptContext(schemes=["bcrypt"])


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
