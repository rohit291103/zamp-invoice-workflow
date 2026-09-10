"""Normalisation and the deterministic fallback extractor."""
from __future__ import annotations

import pytest

from app.extractors import heuristic
from app.pipeline import _clean_currency, _clean_date, _clean_money, normalize


@pytest.mark.parametrize("raw,expected", [
    (4250.00, 4250.00),
    ("4,250.00", 4250.00),
    ("USD 4,250.00", 4250.00),
    ("$1 234.56", 1234.56),
    ("(500.00)", -500.00),      # accounting negative
    ("", None),
    (None, None),
    ("n/a", None),
])
def test_money_cleaning(raw, expected):
    assert _clean_money(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("2026-08-28", "2026-08-28"),
    ("28/08/2026", "2026-08-28"),
    ("28-Aug-2026", "2026-08-28"),
    ("Invoiced on 2026-08-28.", "2026-08-28"),
    ("not a date", None),
    (None, None),
])
def test_date_cleaning(raw, expected):
    assert _clean_date(raw) == expected


@pytest.mark.parametrize("raw,text,expected", [
    ("USD", "", "USD"),
    ("usd", "", "USD"),
    ("$", "", "USD"),
    ("€", "", "EUR"),
    (None, "Total Due: €1,880.50", "EUR"),   # recovered from the document text
    (None, "no symbols here", None),
])
def test_currency_cleaning(raw, text, expected):
    assert _clean_currency(raw, text) == expected


def test_subtotal_is_derived_when_only_line_items_are_stated():
    inv, notes = normalize({
        "vendor_name": "Vertex Packaging Co", "invoice_total": 12000.00,
        "line_items": [{"description": "boxes", "amount": 9000.00},
                       {"description": "pallets", "amount": 3000.00}],
    }, "")
    assert inv.subtotal == 12000.00
    assert any("derived" in n for n in notes)


def test_embedded_tax_is_recovered_rather_than_left_null():
    inv, notes = normalize({
        "vendor_name": "X", "subtotal": 10000.00, "tax_amount": None,
        "invoice_total": 11800.00, "line_items": [],
    }, "")
    assert inv.tax_amount == 1800.00
    assert any("Tax not stated" in n for n in notes)


def test_unparseable_date_is_reported_not_swallowed():
    inv, notes = normalize({"vendor_name": "X", "invoice_date": "the 3rd of never"}, "")
    assert inv.invoice_date is None
    assert any("Could not parse date" in n for n in notes)


def test_blank_strings_become_none_not_empty_fields():
    inv, _ = normalize({"vendor_name": "X", "invoice_number": "", "po_reference": ""}, "")
    assert inv.invoice_number is None and inv.po_reference is None


# --- the fallback path ---------------------------------------------------

SAMPLE = """Kestrel Office Supplies LLC
1120 Marlin Avenue, Austin TX
INVOICE
Invoice #: INV-KS-8841
Invoice Date: 2026-08-28
PO Number: PO-4403
Subtotal 3,150.00
Total Due (USD) 3,150.00
"""


def test_heuristic_extractor_recovers_the_key_fields():
    got = heuristic.extract_from_text(SAMPLE)
    assert got["invoice_number"] == "INV-KS-8841"
    assert got["po_reference"] == "PO-4403"
    assert got["invoice_total"] == 3150.00
    assert got["currency"] == "USD"
    assert got["invoice_date"] == "2026-08-28"


def test_fallback_output_can_never_clear_the_auto_approve_bar():
    """The regex path is a degradation, and it must declare itself as one."""
    from app.config import AUTO_APPROVE_CONFIDENCE
    got = heuristic.extract_from_text(SAMPLE)
    assert all(v < AUTO_APPROVE_CONFIDENCE for v in got["field_confidence"].values())


def test_heuristic_returns_none_rather_than_guessing():
    got = heuristic.extract_from_text("A page with no invoice data on it at all.")
    assert got["invoice_number"] is None
    assert got["invoice_total"] is None
