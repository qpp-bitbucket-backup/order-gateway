"""Client service for managing client-related operations."""
import logging
from typing import List, Optional
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

    def get_cooling_off_seconds(self, store_id: Optional[str]) -> int:
        """
        Get the order cooling-off period (seconds) configured for a client.

        Args:
            store_id: The store ID to look up (None/empty is tolerated)

        Returns:
            Configured cooling-off seconds, or 0 when the client is unknown
            or has no cooling-off configured (order is pushed immediately).
        """
        if not store_id:
            return 0
        with Session(engine) as session:
            client = session.exec(
                select(Client).where(Client.store_id == store_id)
            ).first()
            if client and client.cooling_off_seconds:
                return client.cooling_off_seconds
            return 0

    def get_brand_label_lines(self, store_id: Optional[str]) -> Optional[List[str]]:
        """
        Get the per-client packing label template (one string per printed
        line, ``{sku}`` / ``{product_name}`` placeholders).

        Args:
            store_id: The store ID to look up (None/empty is tolerated)

        Returns:
            The client's template list, or None when the client is unknown or
            has no template configured (caller uses the built-in default).
        """
        if not store_id:
            return None
        with Session(engine) as session:
            client = session.exec(
                select(Client).where(Client.store_id == store_id)
            ).first()
            if client and client.brand_label_lines:
                return client.brand_label_lines
            return None

    def get_brand_label_enabled(self, store_id: Optional[str]) -> bool:
        """
        Whether packing document generation is enabled for a client
        (``clients.enable_brand_label``).

        Args:
            store_id: The store ID to look up (None/empty is tolerated)

        Returns:
            True only when the client exists and has the flag set; unknown
            clients default to False (feature stays off).
        """
        if not store_id:
            return False
        with Session(engine) as session:
            client = session.exec(
                select(Client).where(Client.store_id == store_id)
            ).first()
            return bool(client and client.enable_brand_label)


# Create singleton instance
client_service = ClientService()
