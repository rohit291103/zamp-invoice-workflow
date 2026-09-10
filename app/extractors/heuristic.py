"""Deterministic regex fallback.

This exists so a model outage, a rate limit, or an offline demo degrades the
process instead of stopping it. It is deliberately worse than the model path -
it only handles text-layer PDFs and labels its own output low-confidence - but
"worse and running" beats "nothing" when an AP queue is backing up.
"""
from __future__ import annotations

import re
from typing import Any, Optional

MONEY = r"([0-9][0-9,\s]*\.?[0-9]{0,2})"


def _num(s: Optional[str]) -> Optional[float]:
    if not s:
        return None
    try:
        return float(re.sub(r"[,\s]", "", s))
    except ValueError:
        return None


def _first(text: str, patterns: list[str], must_have_digit: bool = False) -> Optional[str]:
    """First match wins. `must_have_digit` guards identifier fields.

    Without it, `\bINV...` happily matches the word "invoice" itself and
    returns "oice" as an invoice number - a fabricated identifier, which is the
    single worst thing this extractor could produce. Every real invoice and PO
    number contains a digit, so requiring one costs nothing and closes the hole.
    """
    for p in patterns:
        m = re.search(p, text, re.I)
        if m:
            val = m.group(1).strip()
            if must_have_digit and not any(ch.isdigit() for ch in val):
                continue
            return val
    return None


def extract_from_text(text: str) -> dict[str, Any]:
    invoice_number = _first(text, [
        r"invoice\s*(?:no|number|#)[.:\s]*([A-Z0-9][A-Z0-9\-/]{2,})",
        # A separator after INV is required, so the word "invoice" cannot match.
        r"\b(INV[-/][A-Z0-9][A-Z0-9\-/]{2,})",   # capture the INV prefix too
    ], must_have_digit=True)
    po_reference = _first(text, [
        r"\b(PO[-\s]?\d{3,})\b",
        r"p\.?o\.?\s*(?:no|number|#|ref)?[.:\s]*([A-Z0-9\-]{3,})",
    ], must_have_digit=True)
    total = _num(_first(text, [
        rf"(?:total\s*due|amount\s*due|grand\s*total|total\s*payable)[^0-9]{{0,20}}{MONEY}",
        rf"\btotal\b[^0-9]{{0,20}}{MONEY}",
    ]))
    currency = _first(text, [r"\b(USD|EUR|GBP|INR|AUD|CAD)\b"])
    date = _first(text, [
        r"invoice\s*date[.:\s]*(\d{4}-\d{2}-\d{2})",
        r"date[.:\s]*(\d{4}-\d{2}-\d{2})",
    ])
    # Vendor name: the first substantial non-numeric line is the letterhead.
    vendor = None
    for line in text.splitlines():
        s = line.strip()
        if len(s) > 3 and not re.search(r"\d{3}", s) and "invoice" not in s.lower():
            vendor = s
            break

    return {
        "vendor_name": vendor,
        "vendor_tax_id": _first(text, [r"(?:tax\s*id|vat|gstin|ein)[.:\s]*([A-Z0-9\-]{6,})"]),
        "invoice_number": invoice_number,
        "invoice_date": date,
        "po_reference": po_reference,
        "currency": currency,
        "subtotal": _num(_first(text, [rf"sub[\s-]?total[^0-9]{{0,20}}{MONEY}"])),
        "tax_amount": _num(_first(text, [rf"(?:tax|vat|gst)[^0-9]{{0,20}}{MONEY}"])),
        "invoice_total": total,
        "line_items": [],
        # Flat 0.55: high enough to be usable, low enough that nothing
        # extracted this way can clear the auto-approve confidence bar.
        "field_confidence": {k: 0.55 for k in (
            "vendor_name", "invoice_number", "invoice_date",
            "po_reference", "currency", "invoice_total")},
        "extraction_notes": "Deterministic regex fallback - model path unavailable.",
        "_usage": {"model": "heuristic-regex"},
    }
