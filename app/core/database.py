from sqlmodel import SQLModel, create_engine, Session, select
from app.core.config import settings
from app.models.client import Client


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
        echo=settings.DEBUG,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=20,
    )


def create_db_and_tables():
    """Create all database tables."""
    SQLModel.metadata.create_all(engine)
    _ensure_default_client()


def _ensure_default_client():
    """Seed a default client from environment variables when the table is empty."""
    with Session(engine) as session:
        existing = session.exec(select(Client)).first()
        if existing:
            return

        client = Client(
            name="default",
            store_id="default",
            token=settings.ONEFLOW_TOKEN,
            secret=settings.ONEFLOW_SECRET,
            description="Default client bootstrapped from environment variables",
        )
        session.add(client)
        session.commit()


def get_session():
    """Get database session for dependency injection."""
    with Session(engine) as session:
        yield session
