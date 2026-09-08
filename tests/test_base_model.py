"""Unit tests for BaseModel defaults (app/models/base.py) and product task helpers."""
from datetime import datetime, timezone

from sqlmodel import SQLModel

from app.models.base import BaseModel
from app.tasks.products import format_html_desc


class SampleModel(BaseModel):
    """Minimal non-table subclass to exercise BaseModel defaults."""

    name: str = "sample"


class TestBaseModelDefaults:
    """created_at/updated_at must default to timezone-aware UTC timestamps."""

    def test_created_at_is_aware_utc(self):
        before = datetime.now(timezone.utc)
        model = SampleModel()
        after = datetime.now(timezone.utc)
        assert model.created_at.tzinfo == timezone.utc
        assert before <= model.created_at <= after

    def test_updated_at_is_aware_utc(self):
        before = datetime.now(timezone.utc)
        model = SampleModel()
        after = datetime.now(timezone.utc)
        assert model.updated_at.tzinfo == timezone.utc
        assert before <= model.updated_at <= after

    def test_is_active_default_true(self):
        assert SampleModel().is_active is True

    def test_is_a_sqlmodel(self):
        assert issubclass(BaseModel, SQLModel)


class TestFormatHtmlDesc:
    """Test HTML -> plain-text conversion for product descriptions."""

    def test_none_returns_none(self):
        assert format_html_desc(None) is None
        assert format_html_desc("") is None

    def test_plain_text_collapsed(self):
        assert format_html_desc("  Hello   World  ") == "Hello World"

    def test_html_tags_stripped(self):
        assert format_html_desc("<p>Hello <b>World</b></p>") == "Hello World"

    def test_style_block_removed(self):
        result = format_html_desc(
            "<style>body { color: red; }</style><p>Visible text</p>"
        )
        assert result == "Visible text"

    def test_truncated_to_255_chars(self):
        result = format_html_desc("<p>" + "x" * 400 + "</p>")
        assert len(result) == 255
