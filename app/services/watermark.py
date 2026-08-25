"""Watermark service for parallel-card design files.

Parallel-card orders must reach QPMN as images stamped with a
"客供產品" watermark: single-page PDFs are converted to PNG
(300 dpi) after stamping, image files are stamped at their native
resolution. Everything is done with PyMuPDF only — the built-in CJK
font ("china-s") covers the watermark glyphs, so no font asset or
extra dependency is needed.
"""
import logging
import math
import os
import uuid
from typing import Optional

import fitz  # PyMuPDF

logger = logging.getLogger(__name__)

WATERMARK_TEXT = "客供產品"
WATERMARK_COLOR = (1.0, 0.0, 0.0)  # red
WATERMARK_OPACITY = 00.7
WATERMARK_ANGLE_DEG = 45
WATERMARK_DPI = 300
WATERMARK_FONT_SCALE = 3.0 # font-size multiplier on the base size
WATERMARK_LINE_GAP = 1.0  # clear vertical gap between tiled rows = font size x this factor
WATERMARK_X_GAP = 0.1  # horizontal gap between tiled texts = font size x this factor

_font: Optional[fitz.Font] = None


def _get_font() -> fitz.Font:
    """Return the built-in CJK font covering every glyph of WATERMARK_TEXT."""
    global _font
    if _font is None:
        cjk = [ch for ch in WATERMARK_TEXT if ord(ch) > 127]
        for name in ("china-s", "china-t"):
            candidate = fitz.Font(name)
            if all(candidate.has_glyph(ord(ch)) for ch in cjk):
                _font = candidate
                break
        else:
            raise RuntimeError(
                f"No built-in CJK font covers all watermark glyphs: {cjk}"
            )
    return _font


def _tile_watermark(page: "fitz.Page") -> None:
    """Stamp rotated, semi-transparent watermark text tiled over *page*."""
    font = _get_font()
    size = max(16.0, min(page.rect.width, page.rect.height) / 18.0) * WATERMARK_FONT_SCALE
    text_width = font.text_length(WATERMARK_TEXT, fontsize=size)
    # A text run rotated by WATERMARK_ANGLE_DEG spans only
    # cos(angle) x text_width horizontally, so step on that projection to
    # keep the *visual* gap between neighbouring runs at size x WATERMARK_X_GAP
    # (stepping on the full text_width left wide blank vertical stripes).
    # Alternate rows are staggered by half a step so the inter-run gaps of
    # consecutive rows do not line up into stripes.
    step_x = (
        text_width * math.cos(math.radians(WATERMARK_ANGLE_DEG))
        + size * WATERMARK_X_GAP
    )
    # A rotated run also spans tw*sin(angle) + size*cos(angle) vertically,
    # so step on that projection + size x WATERMARK_LINE_GAP of clear space
    # (stepping on size x gap alone made consecutive rows overlap).
    step_y = (
        text_width * math.sin(math.radians(WATERMARK_ANGLE_DEG))
        + size * math.cos(math.radians(WATERMARK_ANGLE_DEG))
        + size * WATERMARK_LINE_GAP
    )

    row = 0
    y = -page.rect.height * 0.25
    while y < page.rect.height * 1.25:
        x = -text_width * 0.5 - (step_x / 2 if row % 2 else 0.0)
        while x < page.rect.width + text_width:
            page.insert_text(
                fitz.Point(x, y),
                WATERMARK_TEXT,
                fontname="china-s",
                fontsize=size,
                color=WATERMARK_COLOR,
                fill_opacity=WATERMARK_OPACITY,
                morph=(fitz.Point(x, y), fitz.Matrix(WATERMARK_ANGLE_DEG)),
                overlay=True,
            )
            x += step_x
        y += step_y
        row += 1


def watermark_to_png(input_path: str, out_dir: str) -> str:
    """
    Stamp the parallel-card watermark on *input_path* and render it to PNG.

    Accepts a single-page PDF or an image file (jpg/jpeg/png). PDFs are
    rendered at ``WATERMARK_DPI``; images keep their native resolution
    (1 image pixel -> 1 output pixel).

    Image files are first wrapped into a single-page PDF — PyMuPDF can only
    draw text (the watermark) on PDF pages, not on image document pages.

    Returns the path of the stamped PNG, or raises on failure.
    """
    base = os.path.splitext(os.path.basename(input_path))[0]
    out_path = os.path.join(out_dir, f"{base}_wm_{uuid.uuid4().hex[:8]}.png")
    ext = os.path.splitext(input_path)[1].lower()

    if ext == ".pdf":
        doc = fitz.open(input_path)
    else:
        # Wrap the image into a PDF page of the same size (1 px = 1 pt)
        src = fitz.open(input_path)
        rect = src[0].rect
        doc = fitz.open()
        page = doc.new_page(width=rect.width, height=rect.height)
        page.insert_image(page.rect, filename=input_path)
        src.close()
    try:
        page = doc[0]
        _tile_watermark(page)

        if ext == ".pdf":
            pix = page.get_pixmap(dpi=WATERMARK_DPI)
        else:
            # Page points == image pixels, render at base 72 dpi for 1:1
            pix = page.get_pixmap(dpi=72)
        pix.save(out_path)
    finally:
        doc.close()

    logger.info(
        f"[WatermarkService] Stamped '{WATERMARK_TEXT}' on {os.path.basename(input_path)} "
        f"-> {out_path} ({os.path.getsize(out_path)} bytes)"
    )
    return out_path


# Singleton-style module API
class WatermarkService:
    """Namespace wrapper so callers use a service-style import."""

    watermark_to_png = staticmethod(watermark_to_png)


watermark_service = WatermarkService()
