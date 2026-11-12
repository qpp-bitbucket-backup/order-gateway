"""Resolve third-party SKU references to internal Sku rows.

``Sku.source_sku`` stores a REGEX PATTERN. An incoming third-party SKU
string from order items resolves to a Sku row when it ``re.fullmatch``es
the pattern. A literal equality check runs first so plain (non-pattern)
values keep the historical exact-match semantics and the DB index stays
usable; pattern evaluation then walks the remaining candidate rows.
"""
import logging
import re
from typing import Any, Dict, List, Optional

from sqlmodel import Session, select

from app.models.product import Sku

logger = logging.getLogger(__name__)


def design_file_key(item: Dict[str, Any], sku_id: Optional[str], item_index: int) -> str:
    """order.files key for one line item's uploaded design files.

    New orders may carry the third-party line-item id in
    items[].sourceItemId — when present it IS the key (stable across
    re-submissions and SKU table edits, unique per line). Otherwise fall
    back to the historical ``{resolved_sku_id}-{item_index}`` composite.

    Writer (publish task) and readers (push payload builder, packing
    documents) must all derive the key through this helper so both sides
    stay in sync.
    """
    source_item_id = (item or {}).get("sourceItemId")
    if source_item_id is not None and str(source_item_id).strip():
        return str(source_item_id)
    return f"{sku_id}-{item_index}"


def design_file_keys(
    item: Dict[str, Any], sku_id: Optional[str], item_index: int,
) -> List[str]:
    """Candidate ``order.files`` keys for one line item, best match first.

    Readers prefer the third-party item id (externalId / sourceItemId) and
    only fall back to the historical ``{resolved_sku_id}-{item_index}``
    composite when that key holds no files — e.g. orders of a store that
    stopped sending sourceItemId, or rows uploaded before the switch.
    Neither key is shared between two items, so the fallback stay safe.
    """
    primary = design_file_key(item, sku_id, item_index)
    composite = f"{sku_id}-{item_index}"
    return [primary] if primary == composite else [primary, composite]


def resolve_design_files(
    files_dict: Dict[str, Any],
    item: Dict[str, Any],
    sku_id: Optional[str],
    item_index: int,
) -> List[Dict[str, Any]]:
    """Pick the item's uploaded design files out of ``order.files``.

    Tries each key from ``design_file_keys`` in order (externalId first,
    composite second) and returns the first non-empty list, or ``[]``.
    """
    for key in design_file_keys(item, sku_id, item_index):
        files = (files_dict or {}).get(key)
        if files:
            return files
    return []


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


PARALLEL_CARD_SKU_SUFFIX = "-PARALLEL"


def find_parallel_card_sku(
    session: Session,
    sku_ref: str,
    *,
    store_id: Optional[str] = None,
) -> Optional[Sku]:
    """Resolve a SKU for a parallel-card line item.

    Parallel-card items carry the same ``items[].sku`` as the base card. A
    store can route them to a parallel-specific product by configuring a
    ``source_sku`` equal to the base ref + ``-PARALLEL`` (literal or regex
    pattern). The suffixed ref is tried first (same resolution order as
    ``find_sku_by_ref``: internal sku_id -> literal -> regex); when it
    matches nothing the plain ref is resolved as usual, so stores without
    a parallel-specific SKU keep getting the base-card product.
    """
    return (
        find_sku_by_ref(
            session,
            f"{sku_ref}{PARALLEL_CARD_SKU_SUFFIX}",
            active_only=False,
            match_internal_id=True,
            store_id=store_id,
        )
        or find_sku_by_ref(
            session,
            sku_ref,
            active_only=False,
            match_internal_id=True,
            store_id=store_id,
        )
    )
