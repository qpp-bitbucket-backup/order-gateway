"""Unit tests for packing document PDF generation (app/services/packing.py)."""
import os

import fitz  # PyMuPDF
import pytest

from app.services.packing import (
    build_label_pdf,
    build_packing_slip_pdf,
    label_filename,
    label_file_key,
    packing_slip_filename,
)


def _norm(text: str) -> str:
    """Normalise PyMuPDF extraction quirks seen with the built-in fallback
    fonts (helv/china-s on hosts without Arial/SimHei): spaces surface as
    U+00A0 and hyphens as U+00AD soft hyphens. Display is unaffected."""
    return text.replace("\xa0", " ").replace("\xad", "-")


# Default label template for tests — mirrors the per-client template stored
# in clients.brand_label_lines (half-width colons, {sku}/{product_name}
# placeholders). build_label_pdf has no built-in fallback anymore.
_DEFAULT_TEMPLATE = [
    "品牌: Topps",
    "产品货号: {sku}",
    "品名: {product_name}",
    "规格: 张（数量以订单为准）",
    "产地: 中国",
    "执行标准: GB6675.1-2014,GB6675.2-2014",
    "GB6675.3-2014,GB6675.4-2014",
    "适用年龄: 8岁以上",
    "出品方: 凡特西艺术珍藏品（上海）有限公司",
    "地址: 中国（上海）自由贸易试验区张杨路707号二层西区205室",
]


def _render(tmp_path, **kwargs):
    dest = str(tmp_path / "label.pdf")
    kwargs.setdefault("template_lines", list(_DEFAULT_TEMPLATE))
    ok = build_label_pdf(dest, **kwargs)
    assert ok is True
    assert os.path.exists(dest)
    doc = fitz.open(dest)
    try:
        text = _norm(doc[0].get_text())
        rect = doc[0].rect
        spans = [
            (_norm(span["text"]), span["color"], span["font"])
            for block in doc[0].get_text("dict")["blocks"]
            for line in block.get("lines", [])
            for span in line.get("spans", [])
        ]
    finally:
        doc.close()
    return text, rect, spans


def test_label_contains_variables_and_fixed_lines(tmp_path):
    text, _, _ = _render(
        tmp_path,
        product_name="2026 Topps Chrome Baseball - Mega",
        sku="FGC006630",
    )
    # variable lines
    assert "FGC006630" in text
    assert "2026 Topps Chrome Baseball - Mega" in text
    # fixed lines (wrapped lines insert newlines — compare whitespace-stripped;
    # joined strips ALL whitespace, so no space after the colon). Assertions
    # avoid full-width punctuation regions: PyMuPDF extraction can insert
    # phantom non-whitespace chars around them.
    joined = "".join(text.split())
    assert "品牌: Topps" in text
    assert "规格:张" in joined
    assert "数量以订单为准" in joined
    assert "产地: 中国" in text
    assert "GB6675.1-2014,GB6675.2-2014" in text
    assert "GB6675.3-2014,GB6675.4-2014" in text
    assert "适用年龄: 8岁以上" in text
    assert "出品方:凡特西艺术珍藏品" in joined
    assert "有限公司" in joined
    assert "地址:中国" in joined
    assert "张杨路707号二层西区205室" in joined


def test_all_label_text_renders_in_black(tmp_path):
    _, _, spans = _render(
        tmp_path,
        product_name="2026 Topps Chrome Baseball - Mega",
        sku="FGC006630",
    )
    # variable values render in black, same as the fixed text (span text may
    # carry extraction quirks — look up by fragment)
    color_of = lambda frag: next((c for t, c, _ in spans if frag in t), None)
    assert color_of("FGC006630") == 0
    assert color_of("2026 Topps Chrome Baseball - Mega") == 0
    # every rendered span — fixed or variable — is black
    assert all(color == 0 for _, color, _ in spans)


def test_mixed_script_line_uses_native_fonts_per_run(tmp_path):
    _, _, spans = _render(tmp_path, product_name="P", sku="S")
    # script runs split at CJK/Latin boundaries — find the runs containing
    # the brand prefix and the latin brand name
    cjk_font = next((font for text, _, font in spans if "品牌" in text), "")
    latin_font = next((font for text, _, font in spans if "Topps" in text), "")
    # CJK run renders in SimHei (or the built-in heiti fallback — PyMuPDF
    # reports its internal name as "Heiti" when no simhei.ttf is installed,
    # e.g. the bare CI image)
    cjk_lower = cjk_font.lower()
    assert "simhei" in cjk_lower or "china" in cjk_lower or "heiti" in cjk_lower
    # Latin run renders in Arial (or the Helvetica fallback)
    assert "Arial" in latin_font or "Helvetica" in latin_font


def test_label_with_missing_product_name(tmp_path):
    text, _, _ = _render(tmp_path, product_name="", sku="PC-123")
    assert "PC-123" in text
    assert "品牌: Topps" in text


def test_label_without_template_is_skipped(tmp_path):
    # no per-client template configured -> label generation is skipped,
    # no file is written (the removed built-in fallback must not return)
    dest = str(tmp_path / "label.pdf")
    assert build_label_pdf(dest, product_name="P", sku="S") is False
    assert not os.path.exists(dest)


def test_label_page_size_matches_settings_mm(tmp_path, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "BRAND_LABEL_WIDTH_MM", 100.0)
    monkeypatch.setattr(settings, "BRAND_LABEL_HEIGHT_MM", 66.8)
    _, rect, _ = _render(tmp_path, product_name="P", sku="S")
    # 100mm x 66.8mm -> 283.46 x 189.35 points
    assert rect.width == pytest.approx(283.46, abs=0.5)
    assert rect.height == pytest.approx(189.35, abs=0.5)


def test_long_sku_still_renders(tmp_path):
    long_sku = "SKU-" + "X" * 120
    text, _, _ = _render(tmp_path, product_name="P", sku=long_sku)
    # wrapping may insert newlines inside the sku — compare whitespace-stripped
    assert long_sku in "".join(text.split())


def test_address_wraps_onto_multiple_lines(tmp_path, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "BRAND_LABEL_WIDTH_MM", 100.0)
    monkeypatch.setattr(settings, "BRAND_LABEL_HEIGHT_MM", 66.8)
    dest = str(tmp_path / "label.pdf")
    assert build_label_pdf(dest, product_name="P", sku="S",
                           template_lines=list(_DEFAULT_TEMPLATE)) is True
    doc = fitz.open(dest)
    try:
        lines = [
            _norm("".join(span["text"] for span in line.get("spans", [])))
            for block in doc[0].get_text("dict")["blocks"]
            for line in block.get("lines", [])
        ]
    finally:
        doc.close()
    # the address no longer fits one 100mm line — it must have wrapped.
    # the break may land anywhere (e.g. "…张杨路707" / "号二层西区205室"),
    # so check short fragments that survive any break point.
    text_all = "\n".join(lines)
    assert "地址:" in text_all
    assert "张杨路" in text_all
    assert "707" in text_all
    assert "号二层西区" in text_all
    assert "205室" in text_all
    # 10 template lines in, more physical lines out -> wrapping happened
    assert len(lines) > len(_DEFAULT_TEMPLATE)


def test_helpers():
    assert label_filename("CN-TEST20260811-1_S1055552", 0) == "CN-TEST20260811-1_S1055552_label_1.pdf"
    # unsafe characters are replaced
    assert label_filename("order/with spaces", 2) == "order_with_spaces_label_3.pdf"
    assert label_file_key(0) == "label-0"
    assert label_file_key(7) == "label-7"
    assert packing_slip_filename("SO123456789") == "SO123456789_packing_slip.pdf"


def _build_slip(tmp_path, **overrides):
    kwargs = dict(
        order_number="SO123456789",
        date_str="01/01/2026",
        shipping_method="SF",
        ship_from_lines=["DG Factory", "Springfield, USA 12345"],
        ship_to_lines=[
            "213 Main Street,",
            "Springfield, USA 54321",
            "Phone: +1-012-345-6789",
            "Attn: Emily Taylor",
        ],
        item_rows=[
            {
                "line": "1 of 2",
                "sku": "ARTF1-16xxx-xxxx-xxxx",
                "product_name": "Kimi Antonelli - 2026 xxxx",
                "qty": 5,
                "image_path": None,
            },
            {
                "line": "2 of 2",
                "sku": "ARTF1-16xxx-xxxx-xxxy",
                "product_name": "Another Driver - 2026 xxxx",
                "qty": 3,
                "image_path": None,
            },
        ],
        total_qty=8,
    )
    kwargs.update(overrides)
    dest = str(tmp_path / "slip.pdf")
    ok = build_packing_slip_pdf(dest, **kwargs)
    assert ok is True
    doc = fitz.open(dest)
    try:
        text = _norm("".join(page.get_text() for page in doc))
        page_rect = doc[0].rect
        n_pages = len(doc)
    finally:
        doc.close()
    return text, page_rect, n_pages


def test_packing_slip_contains_all_sections(tmp_path):
    text, _, _ = _build_slip(tmp_path)
    # title + meta (meta renders label and value in separate columns with
    # no colon; extraction may split them onto separate lines)
    assert "PACKING LIST" in text
    assert "Date" in text and "01/01/2026" in text
    assert "Order Number" in text and "SO123456789" in text
    assert "Shipping Method" in text and "SF" in text
    # shipping information
    assert "Ship from Address" in text
    assert "Ship to Address" in text
    assert "DG Factory" in text
    assert "213 Main Street," in text
    assert "Phone: +1-012-345-6789" in text
    assert "Attn: Emily Taylor" in text
    # order details
    assert "Line" in text and "SKU" in text and "Image" in text and "Qty (c)" in text
    assert "1 of 2" in text and "2 of 2" in text
    assert "ARTF1-16xxx-xxxx-xxxx" in text
    assert "Kimi Antonelli - 2026 xxxx" in text
    assert "Total Qty" in text


def test_packing_slip_is_a4_single_page_for_few_rows(tmp_path):
    _, rect, n_pages = _build_slip(tmp_path)
    assert n_pages == 1
    # A4 -> 595.28 x 841.89 points
    assert rect.width == pytest.approx(595.28, abs=0.5)
    assert rect.height == pytest.approx(841.89, abs=0.5)


def test_packing_slip_paginates_many_rows(tmp_path):
    rows = [
        {"line": f"{i + 1} of 30", "sku": f"SKU-{i}", "product_name": "P", "qty": 1, "image_path": None}
        for i in range(30)
    ]
    _, _, n_pages = _build_slip(tmp_path, item_rows=rows, total_qty=30)
    assert n_pages > 1


def test_packing_slip_with_image(tmp_path):
    # build a small PNG via fitz
    png_path = str(tmp_path / "thumb.png")
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 60, 40))
    pix.save(png_path)
    rows = [{
        "line": "1 of 1",
        "sku": "ARTF1",
        "product_name": "P",
        "qty": 5,
        "image_path": png_path,
    }]
    text, _, _ = _build_slip(tmp_path, item_rows=rows, total_qty=5)
    assert "1 of 1" in text
