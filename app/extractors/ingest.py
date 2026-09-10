"""Stage 1 - work out what kind of document we are actually holding.

The single most consequential branch in the whole process: a PDF with a text
layer can be read exactly, an image of a page can only be interpreted. Those
two facts deserve different amounts of trust downstream.
"""
from __future__ import annotations

import base64
import hashlib
import io
from pathlib import Path
from typing import Any

import pdfplumber
import pypdfium2 as pdfium

# Below this many extractable characters we treat the page as a picture.
# A born-digital invoice runs to hundreds of characters; a scan yields ~0.
TEXT_LAYER_MIN_CHARS = 40


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def render_page_png(path: Path, page_index: int = 0, scale: float = 2.0) -> bytes:
    """Rasterise a page so the vision path has something to look at."""
    pdf = pdfium.PdfDocument(str(path))
    try:
        bitmap = pdf[page_index].render(scale=scale)
        buf = io.BytesIO()
        bitmap.to_pil().save(buf, format="PNG")
        return buf.getvalue()
    finally:
        pdf.close()


def ingest(path: Path) -> dict[str, Any]:
    text_parts: list[str] = []
    with pdfplumber.open(path) as pdf:
        page_count = len(pdf.pages)
        for page in pdf.pages:
            text_parts.append(page.extract_text() or "")
    text = "\n".join(text_parts).strip()

    has_text_layer = len(text) >= TEXT_LAYER_MIN_CHARS
    result: dict[str, Any] = {
        "file_name": path.name,
        "file_sha256": sha256_of(path),
        "size_bytes": path.stat().st_size,
        "page_count": page_count,
        "char_count": len(text),
        "has_text_layer": has_text_layer,
        "mode": "text_layer" if has_text_layer else "image_vision",
        "text": text,
        "page_png_b64": None,
    }
    if not has_text_layer:
        result["page_png_b64"] = base64.b64encode(render_page_png(path)).decode()
    return result
