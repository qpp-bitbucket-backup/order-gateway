"""Unit tests for scripts/alembic_doctor.py chain detection (no DB needed)."""
from scripts.alembic_doctor import detect_actual_revision


class FakeInspector:
    """Stand-in for sqlalchemy Inspector with a hand-crafted schema."""

    def __init__(self, tables, columns, index_names):
        self._tables = tables
        self._columns = columns  # {table: [column names]}
        self._index_names = index_names  # {table: [index names]}

    def get_table_names(self):
        return list(self._tables)

    def get_columns(self, table):
        return [{"name": c} for c in self._columns.get(table, [])]

    def get_indexes(self, table, schema=None):
        return [{"name": n} for n in self._index_names.get(table, [])]


class TestDetectActualRevision:
    """The chain must stop at the FIRST unmatched fingerprint."""

    def test_drift_stops_before_default_email(self):
        # notification_config + unique indexes exist, default_recipient_email missing
        insp = FakeInspector(
            tables=["clients", "products", "skus", "notification_email_logs"],
            columns={
                "clients": ["id", "cooling_off_seconds", "notification_config"],
            },
            index_names={
                "products": ["uq_products_store_product_code"],
                "skus": ["uq_skus_store_code"],
            },
        )
        rev, results = detect_actual_revision(insp)
        assert rev == "a2b3c4d5e6f7"
        assert [ok for _, _, ok in results] == [True, True, True, False]

    def test_drift_stops_at_cooling_off(self):
        # only cooling_off_seconds exists - notification_config missing
        insp = FakeInspector(
            tables=["clients", "products", "skus"],
            columns={"clients": ["id", "cooling_off_seconds"]},
            index_names={},
        )
        rev, results = detect_actual_revision(insp)
        assert rev == "c9d0e1f2a3b4"
        assert [ok for _, _, ok in results] == [True, False]

    def test_full_schema_detects_head(self):
        insp = FakeInspector(
            tables=["clients", "products", "skus", "notification_email_logs"],
            columns={
                "clients": [
                    "id", "cooling_off_seconds", "notification_config",
                    "default_recipient_email",
                ],
            },
            index_names={
                "products": ["uq_products_store_product_code"],
                "skus": ["uq_skus_store_code"],
            },
        )
        rev, results = detect_actual_revision(insp)
        assert rev == "b3c4d5e6f7a8"
        assert all(ok for _, _, ok in results)

    def test_column_without_table_does_not_count(self):
        # d5e6f7a8b9c0 needs BOTH the column and the table; a hand-added
        # column alone must not let the chain advance past a partial state.
        insp = FakeInspector(
            tables=["clients", "products", "skus"],  # no notification_email_logs
            columns={"clients": ["id", "cooling_off_seconds", "notification_config"]},
            index_names={},
        )
        rev, results = detect_actual_revision(insp)
        assert rev == "c9d0e1f2a3b4"
        assert [ok for _, _, ok in results] == [True, False]
