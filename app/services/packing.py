"""Packing document PDF generation for the Topps order flow.

Two document kinds, both gated per client by ``clients.enable_brand_label``
and built during order validation (``tasks.orders.validate_order``):

1. Packing label — one per order line item, sized
   ``BRAND_LABEL_WIDTH_MM`` x ``BRAND_LABEL_HEIGHT_MM``. Fixed legal/
   marketing body plus two variable values from the line item —
   产品货号 (SKU: item description, falling back to the item sku) and
   品名 (productName: item extraData productName, empty when missing) —
   matching the QPMN line-item ``metaData`` conventions.

2. Packing slip — one A4 packing list per order (PACKING LIST header,
   date / order number / shipping method, ship-from + ship-to address
   table, and a Line / SKU / Image / Qty (c) item table with Total Qty).

Typography: mixed Chinese/Latin lines are split into script runs —
Chinese runs render in 黑体 SimHei (embedded from simhei.ttf when
available, the built-in heiti "china-s" as fallback), Latin runs in
Arial (embedded from arial.ttf when available, Helvetica as the
metric-compatible built-in fallback). Run-level splitting keeps each
script in its native typeface and the measured widths match the
rendering exactly.
"""
import logging
import os
import re
from typing import List, Optional, Tuple

import fitz  # PyMuPDF

from app.core.config import settings
from app.services.pdf_utils import mm_to_points

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Font resolution: Arial for Latin, SimHei (黑体) for Chinese
# ---------------------------------------------------------------------------

_FONT_DIRS = (
    "C:/Windows/Fonts/",
    "/usr/share/fonts/",
    "/usr/share/fonts/truetype/",
)

_ARIAL_CANDIDATES = ("arial.ttf", "Arial.ttf")
_ARIAL_BOLD_CANDIDATES = ("arialbd.ttf", "Arial Bold.ttf")
_HEITI_CANDIDATES = ("simhei.ttf", "SimHei.ttf")


def _find_font_file(candidates: Tuple[str, ...]) -> Optional[str]:
    for name in candidates:
        for directory in _FONT_DIRS:
            path = directory + name
            if os.path.exists(path):
                return path
    return None


_ARIAL_FILE = _find_font_file(_ARIAL_CANDIDATES)
_ARIAL_BOLD_FILE = _find_font_file(_ARIAL_BOLD_CANDIDATES)
_HEITI_FILE = _find_font_file(_HEITI_CANDIDATES)

_ARIAL_NAME = "Arial"
_ARIAL_BOLD_NAME = "Arial-Bold"
_HEITI_NAME = "SimHei"


def _has_cjk(text: str) -> bool:
    """True when *text* carries CJK ideographs or full-width punctuation."""
    return any(
        "\u4e00" <= ch <= "\u9fff" or "\u3000" <= ch <= "\u303f" or "\uff00" <= ch <= "\uffef"
        for ch in text
    )


# Script-run splitter for mixed Chinese/Latin text: consecutive CJK
# ideograph / full-width punctuation characters form one run, everything
# else (Latin letters, digits, ASCII punctuation, spaces) the other.
_CJK_RUN_CHARS = "\u3000-\u303f\u4e00-\u9fff\uff00-\uffef"
_SCRIPT_RUN_RE = re.compile(rf"[{_CJK_RUN_CHARS}]+|[^{_CJK_RUN_CHARS}]+")


def _script_runs(text: str) -> List[str]:
    """Split *text* into single-script runs (CJK vs Latin)."""
    return _SCRIPT_RUN_RE.findall(text) if text else []


def _resolved_font(text: str, bold: bool = False) -> Tuple[str, Optional[str]]:
    """
    Pick the (fontname, fontfile) pair for *text*: SimHei for CJK runs,
    Arial otherwise (Arial Bold when *bold*); falls back to built-in fonts
    when the host has no font file (helv/hebo are the metric-compatible
    Arial substitutes, china-s the built-in heiti).
    """
    if _has_cjk(text):
        return (_HEITI_NAME, _HEITI_FILE) if _HEITI_FILE else ("china-s", None)
    if bold:
        return (_ARIAL_BOLD_NAME, _ARIAL_BOLD_FILE) if _ARIAL_BOLD_FILE else ("hebo", None)
    return (_ARIAL_NAME, _ARIAL_FILE) if _ARIAL_FILE else ("helv", None)


def _make_font(fontfile: Optional[str], builtin: str) -> fitz.Font:
    try:
        return fitz.Font(fontfile=fontfile) if fontfile else fitz.Font(builtin)
    except Exception:
        return fitz.Font("helv")


_MEASURE_FONTS = {
    _ARIAL_NAME: _make_font(_ARIAL_FILE, "helv"),
    "helv": _make_font(None, "helv"),
    _ARIAL_BOLD_NAME: _make_font(_ARIAL_BOLD_FILE, "hebo"),
    "hebo": _make_font(None, "hebo"),
    _HEITI_NAME: _make_font(_HEITI_FILE, "china-s"),
    "china-s": _make_font(None, "china-s"),
}


def _text_width(text: str, fontsize: float, bold: bool = False) -> float:
    """
    Measured width of *text* in points — mixed-script text is measured
    run-by-run (CJK runs in SimHei metrics, Latin runs in Arial/Bold
    metrics) so the width matches how _insert_text actually renders it.
    """
    runs = _script_runs(text)
    if not runs:
        runs = [text or " "]
    return sum(
        _MEASURE_FONTS[_resolved_font(run, bold)[0]].text_length(run, fontsize=fontsize)
        for run in runs
    )


def _insert_text(page: "fitz.Page", x: float, y: float, text: str, fontsize: float,
                 color: Tuple[float, float, float] = (0, 0, 0),
                 bold: bool = False) -> float:
    """
    Insert *text* starting at (x, y) with per-script font resolution:
    CJK runs render in SimHei, Latin runs in Arial (Arial Bold when
    *bold*), each run advancing x by its own measured width. Returns the
    x position after the last run.
    """
    for run in _script_runs(text) or [text or " "]:
        name, fontfile = _resolved_font(run, bold)
        page.insert_text(
            fitz.Point(x, y), run,
            fontname=name, fontfile=fontfile,
            fontsize=fontsize, color=color,
        )
        x += _MEASURE_FONTS[name].text_length(run, fontsize=fontsize)
    return x


# ---------------------------------------------------------------------------
# Packing label (Topps product label, one per line item)
# ---------------------------------------------------------------------------

_LINE_GAP = 2.0


def _line_fits_size(segments: List[Tuple[str, bool]], max_width: float, base_size: float) -> float:
    """
    Return the largest font size <= *base_size* keeping the whole line
    (all segments concatenated, each measured in its own font) within
    *max_width* points.
    """
    size = base_size
    while size > 3:
        width = sum(_text_width(text or " ", size) for text, _ in segments)
        if width <= max_width:
            return size
        size -= 0.5
    return size


# Wrappable units for line breaking: Latin/alphanumeric runs (incl. common
# sku punctuation) stay whole; every other character (CJK, punctuation)
# breaks individually.
_WORD_UNIT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9@._%+\-/]*|.")


def _wrap_segments(
    segments: List[Tuple[str, bool]],
    max_width: float,
    size: float,
) -> List[List[Tuple[str, bool]]]:
    """
    Greedy-wrap a label line into physical lines fitting *max_width* at
    *size*: Latin words are kept whole, CJK text breaks between any two
    characters. Adjacent units with the same value-flag merge back into a
    single segment per physical line.
    """
    physical: List[List[Tuple[str, bool]]] = []
    current: List[Tuple[str, bool]] = []
    current_w = 0.0
    for text, flag in segments:
        for unit in _WORD_UNIT_RE.findall(text):
            if unit.isspace() and not current:
                continue  # no leading space on a wrapped line
            w = _text_width(unit, size)
            if current and current_w + w > max_width:
                physical.append(current)
                current, current_w = [], 0.0
                if unit.isspace():
                    continue
            current.append((unit, flag))
            current_w += w
    if current:
        physical.append(current)

    merged: List[List[Tuple[str, bool]]] = []
    for line in physical:
        out: List[Tuple[str, bool]] = []
        for unit, flag in line:
            if out and out[-1][1] == flag:
                out[-1] = (out[-1][0] + unit, flag)
            else:
                out.append((unit, flag))
        merged.append(out)
    return merged


def build_label_pdf(
    dest_path: str,
    *,
    product_name: str,
    sku: str,
    template_lines: Optional[List[str]] = None,
) -> bool:
    """
    Render one packing label PDF to *dest_path*.

    ``template_lines`` is the per-client label template stored on the clients
    row (``brand_label_lines``): one string per printed line with ``{sku}``
    and ``{product_name}`` placeholders, substituted via str.replace (not
    str.format) so braces inside product names cannot crash rendering.
    ``None`` or an empty list means the client has no template configured —
    the label is skipped and this function returns False (caller logs it).

    Returns:
        True on success, False on failure / missing template.
    """
    # Per-client template is mandatory for label generation; missing template
    # means the client hasn't configured one — caller logs and skips this
    # line item rather than synthesising a fallback (removed _label_lines).
    if not template_lines:
        return False
    try:
        width = mm_to_points(settings.BRAND_LABEL_WIDTH_MM)
        height = mm_to_points(settings.BRAND_LABEL_HEIGHT_MM)
        margin_top = 14.0
        margin_right = 18.0
        margin_bottom = 4.0
        margin_left = 18.0
        max_text_width = width - margin_left - margin_right

        wrap_size = 12.0
        lines: List[List[Tuple[str, bool]]] = []
        sku_text = str(sku or "")
        name_text = str(product_name or "")
        name_wrapped_lines = 0
        for tpl in template_lines:
            rendered = tpl.replace("{sku}", sku_text).replace("{product_name}", name_text)
            wrapped = _wrap_segments([(rendered, False)], max_text_width, wrap_size)
            if "{product_name}" in tpl:
                # Printed height of the product name itself: when it wraps to
                # more than two lines, reclaim space by shrinking the top margin.
                name_wrapped_lines = len(wrapped)
            lines.extend(wrapped)
        if name_wrapped_lines >= 2:
            margin_top -= (name_wrapped_lines-1) * 5
        n = len(lines)

        # Base size fits all lines (plus gaps) within the page height,
        # capped at wrap_size so labels keep a document-like density.
        base_size = min(
            wrap_size,
            (height - margin_top - margin_bottom - (n - 1) * _LINE_GAP) / n,
        )

        doc = fitz.open()
        page = doc.new_page(width=width, height=height)

        y = margin_top + base_size
        for segments in lines:
            size = _line_fits_size(segments, max_text_width, base_size)
            x = margin_left
            for text, _is_value in segments:
                if text:
                    x = _insert_text(page, x, y, text, size)
            y += size + _LINE_GAP

        doc.save(dest_path, garbage=3, deflate=True)
        doc.close()
        logger.info(
            "[Packing] Generated label %s (%s x %s mm, %d line(s))",
            os.path.basename(dest_path),
            settings.BRAND_LABEL_WIDTH_MM,
            settings.BRAND_LABEL_HEIGHT_MM,
            n,
        )
        return True
    except Exception as exc:
        logger.error("[Packing] Failed to generate label %s: %s", dest_path, exc, exc_info=True)
        return False


def safe_source_ref(source_order_id: str) -> str:
    """Filesystem-safe form of a source order id (used in filenames/dirs)."""
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in source_order_id)


def label_filename(source_order_id: str, item_index: int) -> str:
    """
    Filename for a label: ``{source_order_id}_label_{n}.pdf`` with the
    1-based line-item position — keeps labels traceable to the source
    order without printing the order id on the label itself.
    """
    return f"{safe_source_ref(source_order_id)}_label_{item_index + 1}.pdf"


def label_file_key(item_index: int) -> str:
    """
    order.files dict key for a label: ``label-{item_index}`` (0-based, the
    same indexing as the design-file ``{sku_id}-{item_index}`` keys).
    """
    return f"label-{item_index}"


# ---------------------------------------------------------------------------
# Packing slip (A4 shipping document)
# ---------------------------------------------------------------------------

# order.files dict key for the single packing slip per order
PACKING_SLIP_FILE_KEY = "packing-slip"

_A4_PT = (595.28, 841.89)
_SLIP_MARGIN = 40.0
_SLIP_HEADER_H = 16.0
_SLIP_ROW_H = 55.0
_SLIP_COLS = (("Line", 75.0), ("SKU", 235.0), ("Image", 140.0), ("Qty (c)", 65.28))
_SLIP_GRAY = (0.92, 0.92, 0.92)


def packing_slip_filename(source_order_id: str) -> str:
    """Filename for the order's packing slip: ``{source_order_id}_packing_slip.pdf``."""
    return f"{safe_source_ref(source_order_id)}_packing_slip.pdf"


def _insert_image_fit(page: "fitz.Page", image_path: str, cell: "fitz.Rect") -> None:
    """
    Insert an image (or the first page of a design PDF) into *cell*,
    aspect-preserving and centered. Silently skips on any failure.
    """
    try:
        doc = fitz.open(image_path)
        try:
            if doc.is_pdf:
                rect = doc[0].rect
                w, h = rect.width or 1.0, rect.height or 1.0
            else:
                pix = fitz.Pixmap(image_path)
                w, h = pix.width or 1, pix.height or 1
        finally:
            doc.close()
        pad = 4.0
        inner = fitz.Rect(cell.x0 + pad, cell.y0 + pad, cell.x1 - pad, cell.y1 - pad)
        scale = min(inner.width / w, inner.height / h)
        fw, fh = w * scale, h * scale
        cx, cy = (inner.x0 + inner.x1) / 2, (inner.y0 + inner.y1) / 2
        page.insert_image(fitz.Rect(cx - fw / 2, cy - fh / 2, cx + fw / 2, cy + fh / 2), filename=image_path)
    except Exception as exc:
        logger.warning("[Packing] Skipping slip image %s: %s", image_path, exc)


def build_packing_slip_pdf(
    dest_path: str,
    *,
    order_number: str,
    date_str: str,
    shipping_method: str,
    ship_from_lines: List[str],
    ship_to_lines: List[str],
    item_rows: List[dict],
    total_qty: int,
) -> bool:
    """
    Render the A4 packing slip (packing list) to *dest_path*.

    Args:
        order_number:     source order id shown as "Order Number".
        date_str:         preformatted date string (e.g. 01/01/2026).
        shipping_method:  shipping method name (e.g. SF / Standard).
        ship_from_lines:  fixed ship-from block (from BRAND_LABEL_SHIP_FROM).
        ship_to_lines:    delivery address lines (street / locality /
                          Phone / Attn).
        item_rows:        one dict per line item: ``line`` ("1 of 3"),
                          ``sku``, ``product_name``, ``qty``,
                          ``image_path`` (optional local image/design PDF).
        total_qty:        sum of all item quantities.

    Returns:
        True on success, False on failure (caller logs and skips the slip).
    """
    try:
        page_w, page_h = _A4_PT
        m = _SLIP_MARGIN

        doc = fitz.open()
        page = doc.new_page(width=page_w, height=page_h)

        # --- Title (left-aligned) ---
        y = m + 20
        _insert_text(page, m, y, "PACKING LIST", 20)
        y += 30

        # --- Meta block (bold labels, value column aligned) ---
        meta_items = (
            ("Date", date_str),
            ("Order Number", order_number),
            ("Shipping Method", shipping_method),
        )
        label_col_w = max(_text_width(lbl, 10, bold=True) for lbl, _ in meta_items)
        for label, value in meta_items:
            _insert_text(page, m, y, label, 10, bold=True)
            _insert_text(page, m + label_col_w + 10, y, str(value), 10)
            y += 15
        y += 12

        # --- Shipping Information (two-column address table, no gap
        # between the cells) ---
        # _insert_text(page, m, y, "Shipping Information", 12)
        y += 8
        cell_w = (page_w - 2 * m) / 2
        body_h = max(len(ship_from_lines), len(ship_to_lines), 1) * 12 + 10
        for i, header in enumerate(("Ship from Address", "Ship to Address")):
            x = m + i * cell_w
            page.draw_rect(fitz.Rect(x, y, x + cell_w, y + _SLIP_HEADER_H), fill=_SLIP_GRAY, color=(0, 0, 0), width=0.7)
            _insert_text(page, x + 6, y + 11.5, header, 9, bold=True)
        y += _SLIP_HEADER_H
        for i, lines in enumerate((ship_from_lines, ship_to_lines)):
            x = m + i * cell_w
            page.draw_rect(fitz.Rect(x, y, x + cell_w, y + body_h), color=(0, 0, 0), width=0.7)
            ty = y + 13
            for ln in lines:
                size = _line_fits_size([(ln, False)], cell_w - 12, 9.0)
                _insert_text(page, x + 6, ty, ln, size)
                ty += 12
        y += body_h + 20

        # --- Order Details table ---
        # _insert_text(page, m, y, "Order Details", 12)
        y += 8

        def draw_table_header(p: "fitz.Page", top: float) -> float:
            x = m
            for name, cw in _SLIP_COLS:
                p.draw_rect(fitz.Rect(x, top, x + cw, top + _SLIP_HEADER_H), fill=_SLIP_GRAY, color=(0, 0, 0), width=0.7)
                _insert_text(p, x + 6, top + 11.5, name, 9, bold=True)
                x += cw
            return top + _SLIP_HEADER_H

        y = draw_table_header(page, y)
        col_x = [m]
        for _, cw in _SLIP_COLS:
            col_x.append(col_x[-1] + cw)

        for row in item_rows:
            if y + _SLIP_ROW_H > page_h - m:
                page = doc.new_page(width=page_w, height=page_h)
                y = draw_table_header(page, m)
            # cell borders
            x = m
            for _, cw in _SLIP_COLS:
                page.draw_rect(fitz.Rect(x, y, x + cw, y + _SLIP_ROW_H), color=(0, 0, 0), width=0.7)
                x += cw
            # Line
            line_no = str(row.get("line", ""))
            _insert_text(page, col_x[0] + 6, y + 14, line_no, 9)
            # SKU (+ product name on second line)
            sku_text = str(row.get("sku", ""))
            _insert_text(page, col_x[1] + 6, y + 14, sku_text, 9)
            product_name = str(row.get("product_name", ""))
            if product_name:
                _insert_text(page, col_x[1] + 6, y + 27, product_name, 9)
            # Image
            image_path = row.get("image_path")
            if image_path:
                _insert_image_fit(page, image_path, fitz.Rect(col_x[2] + 1, y + 1, col_x[3] - 1, y + _SLIP_ROW_H - 1))
            # Qty — left-aligned in its column
            qty_text = str(row.get("qty", ""))
            _insert_text(page, col_x[3] + 6, y + 14, qty_text, 9)
            y += _SLIP_ROW_H

        # --- Total row (Line+SKU merged into one white cell; the Image and
        # Qty columns keep the gray header background; "Total Qty" under
        # the Image column, value left-aligned in the Qty column, bold) ---
        total_h = 16.0
        if y + total_h > page_h - m:
            page = doc.new_page(width=page_w, height=page_h)
            y = m
        table_right = m + sum(cw for _, cw in _SLIP_COLS)
        # merged Line+SKU cell — white background
        page.draw_rect(fitz.Rect(m, y, col_x[2], y + total_h), fill=(1, 1, 1), color=(0, 0, 0), width=0.7)
        # Image + Qty cells — gray background
        page.draw_rect(fitz.Rect(col_x[2], y, table_right, y + total_h), fill=_SLIP_GRAY, color=(0, 0, 0), width=0.7)
        # divider between the Image and Qty cells
        page.draw_line(fitz.Point(col_x[3], y), fitz.Point(col_x[3], y + total_h), color=(0, 0, 0), width=0.7)
        _insert_text(page, col_x[2] + 6, y + 11.5, "Total Qty", 9, bold=True)
        _insert_text(page, col_x[3] + 6, y + 11.5, str(total_qty), 9, bold=True)

        doc.save(dest_path, garbage=3, deflate=True)
        doc.close()
        logger.info("[Packing] Generated packing slip %s (A4, %d item row(s))", os.path.basename(dest_path), len(item_rows))
        return True
    except Exception as exc:
        logger.error("[Packing] Failed to generate packing slip %s: %s", dest_path, exc, exc_info=True)
        return False
