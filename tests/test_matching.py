"""Vendor resolution and PO matching - the parts that must tolerate mess."""
from __future__ import annotations

import pytest

from app.masterdata import find_po, resolve_vendor
from app.matching import tolerance_for
from app.masterdata import load_pos


PO = {p.po_number: p for p in load_pos()}


@pytest.mark.parametrize("written,expected", [
    ("PO-4404", "PO-4404"),
    ("PO-4404 / release 1 of 3", "PO-4404"),   # buried in free text
    ("Ref: PO 4401", "PO-4401"),
    ("po#4403", "PO-4403"),
    ("Order 4407", "PO-4407"),
    ("44041", None),                            # not a substring match
    ("nonsense", None),
    ("", None),
    (None, None),
])
def test_po_reference_variants(written, expected):
    po = find_po(written)
    assert (po.po_number if po else None) == expected


@pytest.mark.parametrize("name,expected", [
    ("Acme Steel Works Pvt Ltd", "V-1001"),
    ("ACME STEEL WORKS PVT. LTD.", "V-1001"),   # punctuation and case
    ("Kestrel Office Sup.", "V-1003"),          # truncated alias
    ("NORTHWIND LOGISTICS BV", "V-1002"),
])
def test_vendor_resolution(name, expected):
    vendor, _, _ = resolve_vendor(name)
    assert vendor is not None and vendor.vendor_id == expected


def test_unknown_vendor_is_not_forced_to_a_match():
    """A near-miss must not be silently accepted - we would be inventing a
    commercial relationship that does not exist."""
    vendor, score, how = resolve_vendor("Totally Unrelated Trading Company")
    assert vendor is None
    assert score < 0.80
    assert "80%" in how


def test_tax_id_beats_name():
    """Tax ID is the identifier a vendor cannot typo their way out of."""
    vendor, score, how = resolve_vendor("Some Renamed Entity", "29AABCA1234F1Z5")
    assert vendor.vendor_id == "V-1001"
    assert score == 1.0 and how == "tax_id exact"


def test_tolerance_uses_the_greater_of_percent_and_floor():
    # Large PO -> percentage dominates.
    assert tolerance_for(PO["PO-4401"]) == pytest.approx(972.0)
    # Small PO -> the absolute floor protects it (2% of 890 is only 17.80).
    assert tolerance_for(PO["PO-4406"]) == pytest.approx(25.0)
