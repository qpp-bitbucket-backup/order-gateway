"""Client service for managing client-related operations."""
import logging
from typing import List, Optional, Tuple
from sqlmodel import Session, select
from app.core.config import settings
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

    @staticmethod
    def _release_routing_configured() -> bool:
        """True when the release-routing feature is fully configured
        (marker + target store + both release URLs set)."""
        return bool(
            settings.QPMN_RELEASE_ORDER_MARKER
            and settings.QPMN_RELEASE_STORE_ID
            and settings.QPMN_RELEASE_API_URL
            and settings.QPMN_RELEASE_OPEN_API_URL
        )

    def get_qpmn_urls(self, store_id: Optional[str]) -> Tuple[str, str]:
        """
        Resolve the QPMN base URLs for a store.

        The simulated-production client (store_id ==
        QPMN_RELEASE_STORE_ID) talks to the release platform; every other
        store uses the global stage URLs.

        Returns:
            (api_url, open_api_url)
        """
        if (
            store_id
            and self._release_routing_configured()
            and store_id == settings.QPMN_RELEASE_STORE_ID
        ):
            return settings.QPMN_RELEASE_API_URL, settings.QPMN_RELEASE_OPEN_API_URL
        return settings.QPMN_API_URL, settings.QPMN_OPEN_API_URL

    def resolve_release_routing(self, source_order_id: Optional[str]) -> Optional[str]:
        """
        Order-level release routing (stage testing only).

        When ``sourceOrderId`` contains the configured marker, the order is
        created under the simulated-production client so the whole flow
        (uploads, push, SKUs, cancel/return, currency/shipping lookups)
        runs against the QPMN release platform.

        Routing activates only when ALL four env vars are set; with any of
        them unset the feature is considered disabled and the order is
        created under the authenticated client's store as usual (a warning
        is logged when the marker matched, so partial configuration is
        visible).

        Returns:
            The release store_id when routing applies, None otherwise
            (including the disabled/partial-config fallback).

        Raises:
            ValueError: the marker matched and the feature is fully
                configured, but the simulated-production client is missing,
                inactive or has no store_key — callers must reject the order
                (400) rather than silently falling back to stage.
        """
        marker = settings.QPMN_RELEASE_ORDER_MARKER
        if not marker or not source_order_id or marker not in source_order_id:
            return None
        if not self._release_routing_configured():
            logger.warning(
                "[ClientService] sourceOrderId '%s' carries the release marker "
                "'%s' but release routing is not fully configured (marker / "
                "store / URLs env vars); falling back to the client's own store",
                source_order_id,
                marker,
            )
            return None
        target_store = settings.QPMN_RELEASE_STORE_ID
        with Session(engine) as session:
            client = session.exec(
                select(Client).where(
                    Client.store_id == target_store, Client.is_active == True
                )
            ).first()
        if not client or not client.store_key:
            raise ValueError(
                f"Release routing target client '{target_store}' is missing, "
                "inactive or has no store_key configured"
            )
        return target_store


# Create singleton instance
client_service = ClientService()
