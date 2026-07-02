"""Client service for managing client-related operations."""
import logging
from typing import Optional
from sqlmodel import Session, select
from app.core.database import engine
from app.models.client import Client


logger = logging.getLogger(__name__)


class ClientService:
    """Service for handling client-related operations."""

    def __init__(self):
        """Initialize ClientService."""
        pass

    def get_store_key_by_id(self, store_id: str) -> Optional[str]:
        """
        Get store_key from clients table by store_id.
        
        Args:
            store_id: The store ID to look up
            
        Returns:
            store_key if found, None otherwise
        """
        with Session(engine) as session:
            client = session.exec(
                select(Client).where(Client.store_id == store_id)
            ).first()
            
            if client:
                return client.store_key
            else:
                logger.warning(f"No client found for store_id: {store_id}")
                return None


# Create singleton instance
client_service = ClientService()
