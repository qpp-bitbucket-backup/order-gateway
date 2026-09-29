from sqlmodel import SQLModel, create_engine, Session, select
from app.core.config import settings
from app.models.user import User, UserRole  # noqa: F401 – ensure table creation
from app.core.security import get_password_hash


# Create engine with database connection
connect_args = {}
if "sqlite" in settings.DATABASE_URL:
    connect_args = {"check_same_thread": False}
    engine = create_engine(
        settings.DATABASE_URL,
        echo=settings.DEBUG,
        connect_args=connect_args,
    )
else:
    engine = create_engine(
        settings.DATABASE_URL,
        echo=False,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=20,
    )


def create_db_and_tables():
    """Create all database tables."""
    SQLModel.metadata.create_all(engine)
    _ensure_default_admin()


def _ensure_default_admin():
    """Seed a default admin user from environment variables when the table is empty."""
    with Session(engine) as session:
        existing = session.exec(select(User).where(User.role == UserRole.ADMIN)).first()
        if existing:
            return

        admin = User(
            username=settings.DEFAULT_ADMIN_USERNAME,
            email=settings.DEFAULT_ADMIN_EMAIL,
            hashed_password=get_password_hash(settings.DEFAULT_ADMIN_PASSWORD),
            full_name="System Administrator",
            role=UserRole.ADMIN,
        )
        session.add(admin)
        session.commit()
        print(f"[DB] Default admin user created: {settings.DEFAULT_ADMIN_USERNAME}")


def get_session():
    """Get database session for dependency injection."""
    with Session(engine) as session:
        yield session
