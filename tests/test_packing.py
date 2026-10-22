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


def _render(tmp_path, **kwargs):
    dest = str(tmp_path / "label.pdf")
    ok = build_label_pdf(dest, **kwargs)
    assert ok is True
    assert os.path.exists(dest)
    doc = fitz.open(dest)
    try:
        text = doc[0].get_text()
        rect = doc[0].rect
        spans = [
            (span["text"], span["color"], span["font"])
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
    # fixed lines (wrapped lines insert newlines — compare whitespace-stripped)
    joined = "".join(text.split())
    assert "品牌：Topps" in text
    assert "规格：张（数量以订单为准）" in joined
    assert "产地：中国" in text
    assert "GB6675.1-2014,GB6675.2-2014" in text
    assert "GB6675.3-2014,GB6675.4-2014" in text
    assert "适用年龄：8岁以上" in text
    assert "出品方：凡特西艺术珍藏品（上海）有限公司" in joined
    assert "地址：中国（上海）自由贸易试验区张杨路707号二层西区205室" in joined


def test_all_label_text_renders_in_black(tmp_path):
    _, _, spans = _render(
        tmp_path,
        product_name="2026 Topps Chrome Baseball - Mega",
        sku="FGC006630",
    )
    colors = {text: color for text, color, _ in spans}
    # variable values render in black, same as the fixed text
    assert colors.get("FGC006630") == 0
    assert colors.get("2026 Topps Chrome Baseball - Mega") == 0
    # fixed line (mixed line renders as two runs)
    assert colors.get("品牌：") == 0
    assert colors.get("Topps") == 0


def test_mixed_script_line_uses_native_fonts_per_run(tmp_path):
    _, _, spans = _render(tmp_path, product_name="P", sku="S")
    fonts = {text: font for text, _, font in spans}
    cjk_font = fonts.get("品牌：", "")
    latin_font = fonts.get("Topps", "")
    # CJK run renders in SimHei (or the built-in heiti fallback)
    assert "SimHei" in cjk_font or "china" in cjk_font.lower()
    # Latin run renders in Arial (or the Helvetica fallback)
    assert "Arial" in latin_font or "Helvetica" in latin_font


def test_label_with_missing_product_name(tmp_path):
    text, _, _ = _render(tmp_path, product_name="", sku="PC-123")
    assert "PC-123" in text
    assert "品牌：Topps" in text


def test_label_page_size_matches_settings_mm(tmp_path, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "BRAND_LABEL_WIDTH_MM", 100.0)
    monkeypatch.setattr(settings, "BRAND_LABEL_HEIGHT_MM", 66.8)
    _, rect, _ = _render(tmp_path, product_name="P", sku="S")
    # 100mm x 50mm -> 283.46 x 141.73 points
    assert rect.width == pytest.approx(283.46, abs=0.5)
    assert rect.height == pytest.approx(141.73, abs=0.5)


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
    assert build_label_pdf(dest, product_name="P", sku="S") is True
    doc = fitz.open(dest)
    try:
        lines = [
            "".join(span["text"] for span in line.get("spans", []))
            for block in doc[0].get_text("dict")["blocks"]
            for line in block.get("lines", [])
        ]
    finally:
        doc.close()
    address_lines = [ln for ln in lines if "张杨路707号" in ln or "地址：" in ln]
    # the address no longer fits one 100mm line — it must have wrapped
    assert len(address_lines) >= 2
    assert any("地址：" in ln for ln in address_lines)
    assert any("张杨路707号" in ln for ln in address_lines)


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
        text = "".join(page.get_text() for page in doc)
        page_rect = doc[0].rect
        n_pages = len(doc)
    finally:
        doc.close()
    return text, page_rect, n_pages


def test_packing_slip_contains_all_sections(tmp_path):
    text, _, _ = _build_slip(tmp_path)
    # title + meta
    assert "PACKING LIST" in text
    assert "Date: 01/01/2026" in text
    assert "Order Number: SO123456789" in text
    assert "Shipping Method: SF" in text
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
