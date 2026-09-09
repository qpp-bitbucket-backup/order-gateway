"""Resolve third-party SKU references to internal Sku rows.

``Sku.source_sku`` stores a REGEX PATTERN. An incoming third-party SKU
string from order items resolves to a Sku row when it ``re.fullmatch``es
the pattern. A literal equality check runs first so plain (non-pattern)
values keep the historical exact-match semantics and the DB index stays
usable; pattern evaluation then walks the remaining candidate rows.
"""
import logging
import re
from typing import Optional

from sqlmodel import Session, select

from app.models.product import Sku

logger = logging.getLogger(__name__)


def source_sku_matches(pattern: Optional[str], sku_ref: str) -> bool:
    """Return True when ``sku_ref`` fullmatches the ``source_sku`` pattern.

    A plain literal value still matches exactly (a literal string is a
    valid pattern for itself). Invalid patterns never match and are logged.
    """
    if not pattern:
        return False
    try:
        return re.fullmatch(pattern, sku_ref) is not None
    except re.error:
        logger.warning("[sku_matching] Invalid source_sku regex %r skipped", pattern)
        return False


def find_sku_by_ref(
    session: Session,
    sku_ref: str,
    *,
    active_only: bool = True,
    store_id: Optional[str] = None,
    match_internal_id: bool = False,
) -> Optional[Sku]:
    """Resolve a SKU reference from order items[].sku to a Sku row.

    Args:
        sku_ref: Third-party SKU string or (legacy orders) internal sku_id.
        active_only: Restrict source_sku resolution to active SKUs.
        store_id: Restrict source_sku resolution to the client's store.
        match_internal_id: Also resolve exact internal sku_id matches
            (legacy behaviour of the publish/push paths).

    Resolution order: internal sku_id (when enabled) -> literal source_sku
    equality -> source_sku regex fullmatch.
    """
    if match_internal_id:
        exact_id = session.exec(
            select(Sku).where(Sku.sku_id == sku_ref)
        ).first()
        if exact_id:
            return exact_id

    literal_query = select(Sku).where(Sku.source_sku == sku_ref)
    if active_only:
        literal_query = literal_query.where(Sku.active.is_(True))
    if store_id:
        literal_query = literal_query.where(Sku.store_id == store_id)
    literal = session.exec(literal_query).first()
    if literal:
        return literal

    pattern_query = select(Sku).where(Sku.source_sku.is_not(None))
    if active_only:
        pattern_query = pattern_query.where(Sku.active.is_(True))
    if store_id:
        pattern_query = pattern_query.where(Sku.store_id == store_id)
    for sku in session.exec(pattern_query).all():
        if source_sku_matches(sku.source_sku, sku_ref):
            return sku
    return None
