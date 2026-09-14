"""Unit tests for source_sku regex matching (app/services/sku_matching.py)."""
import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.models.product import Sku
from app.services.sku_matching import find_sku_by_ref, source_sku_matches


@pytest.fixture(name="session")
def session_fixture():
    """Fresh in-memory database with only the registered model tables."""
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def _make_sku(sku_id, code, source_sku=None, active=True, store_id=None) -> Sku:
    return Sku(
        sku_id=sku_id,
        code=code,
        product_id="prod-1",
        source_sku=source_sku,
        active=active,
        store_id=store_id,
    )


class TestSourceSkuMatches:
    """Pure pattern-matching semantics."""

    def test_empty_pattern_never_matches(self):
        assert source_sku_matches(None, "X") is False
        assert source_sku_matches("", "X") is False

    def test_literal_matches_itself(self):
        assert source_sku_matches("TOPPS-CARD-A5", "TOPPS-CARD-A5") is True

    def test_literal_rejects_other_values(self):
        assert source_sku_matches("TOPPS-CARD-A5", "TOPPS-CARD-A6") is False

    def test_regex_pattern_matches(self):
        assert source_sku_matches(r"^TOPPS-CARD-[A-Z0-9]+$", "TOPPS-CARD-A5") is True

    def test_regex_case_sensitive(self):
        assert source_sku_matches(r"^TOPPS-CARD-[A-Z0-9]+$", "topps-card-a5") is False

    def test_fullmatch_requires_entire_string(self):
        # re.fullmatch: trailing extra characters must not match
        assert source_sku_matches("SKU-1", "SKU-12") is False

    def test_alternation_pattern(self):
        assert source_sku_matches(r"CARD-(A5|A6)", "CARD-A6") is True
        assert source_sku_matches(r"CARD-(A5|A6)", "CARD-A7") is False

    def test_invalid_regex_never_matches(self):
        assert source_sku_matches("[unclosed", "X") is False


class TestFindSkuByRef:
    """DB-backed resolution: order, filters and fallback behaviour."""

    def test_internal_id_exact_match(self, session):
        session.add(_make_sku("internal-1", "CODE-1"))
        session.commit()
        found = find_sku_by_ref(session, "internal-1", match_internal_id=True)
        assert found is not None
        assert found.sku_id == "internal-1"

    def test_internal_id_ignored_when_disabled(self, session):
        session.add(_make_sku("internal-2", "CODE-2"))
        session.commit()
        assert find_sku_by_ref(session, "internal-2") is None

    def test_literal_source_sku_match(self, session):
        session.add(_make_sku("s-lit", "CODE-3", source_sku="LITERAL-SKU"))
        session.commit()
        found = find_sku_by_ref(session, "LITERAL-SKU")
        assert found is not None
        assert found.sku_id == "s-lit"

    def test_regex_source_sku_match(self, session):
        session.add(_make_sku("s-re", "CODE-4", source_sku=r"^PC-\d{4}$"))
        session.commit()
        found = find_sku_by_ref(session, "PC-1234")
        assert found is not None
        assert found.sku_id == "s-re"

    def test_regex_no_match_returns_none(self, session):
        session.add(_make_sku("s-re2", "CODE-5", source_sku=r"^PC-\d{4}$"))
        session.commit()
        assert find_sku_by_ref(session, "PC-12") is None

    def test_active_only_filters_inactive(self, session):
        session.add(_make_sku("s-inact", "CODE-6", source_sku=r"^IN-\d+$", active=False))
        session.commit()
        assert find_sku_by_ref(session, "IN-1") is None
        found = find_sku_by_ref(session, "IN-1", active_only=False)
        assert found is not None

    def test_store_scoping(self, session):
        session.add(_make_sku("s-storea", "CODE-7", source_sku=r"^ST-\d+$", store_id="store-A"))
        session.commit()
        assert find_sku_by_ref(session, "ST-1", store_id="store-B") is None
        found = find_sku_by_ref(session, "ST-1", store_id="store-A")
        assert found is not None

    def test_invalid_pattern_skipped_falls_through(self, session):
        session.add(_make_sku("s-bad", "CODE-8", source_sku="[unclosed"))
        session.add(_make_sku("s-good", "CODE-9", source_sku=r"^OK-\d+$"))
        session.commit()
        found = find_sku_by_ref(session, "OK-7")
        assert found is not None
        assert found.sku_id == "s-good"

    def test_literal_wins_over_pattern(self, session):
        session.add(_make_sku("s-pat", "CODE-10", source_sku=r"^X-\d$"))
        session.add(_make_sku("s-lit2", "CODE-11", source_sku="X-1"))
        session.commit()
        found = find_sku_by_ref(session, "X-1")
        assert found is not None
        assert found.sku_id == "s-lit2"
