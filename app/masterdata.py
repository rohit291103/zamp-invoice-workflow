"""Loads the procurement master data the process validates against.

CSV stands in for what would be an ERP/procurement API in production. The
resolver logic (fuzzy vendor naming, PO inference) is the part that would
survive that swap unchanged.
"""
from __future__ import annotations

import csv
import re
from difflib import SequenceMatcher
from typing import Optional

from .config import DATA_DIR
from .models import PurchaseOrder, Vendor


def _norm(s: str) -> str:
    """Strip punctuation and legal suffixes so 'Acme Steel Works Pvt Ltd'
    and 'ACME STEEL WORKS PVT. LTD.' compare equal."""
    s = (s or "").lower()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(
        r"\b(pvt|private|ltd|limited|llc|inc|incorporated|co|corp|corporation|bv|b v|gmbh|plc|group|grp)\b",
        " ", s,
    )
    return re.sub(r"\s+", " ", s).strip()


def load_vendors() -> list[Vendor]:
    with open(DATA_DIR / "vendors.csv", newline="", encoding="utf-8") as fh:
        return [
            Vendor(
                vendor_id=r["vendor_id"],
                legal_name=r["legal_name"],
                aliases=[a for a in r["aliases"].split("|") if a],
                status=r["status"],
                status_reason=r["status_reason"],
                tax_id=r["tax_id"],
                country=r["country"],
                default_currency=r["default_currency"],
                payment_terms=r["payment_terms"],
            )
            for r in csv.DictReader(fh)
        ]


def load_pos() -> list[PurchaseOrder]:
    with open(DATA_DIR / "purchase_orders.csv", newline="", encoding="utf-8") as fh:
        return [
            PurchaseOrder(
                po_number=r["po_number"],
                vendor_id=r["vendor_id"],
                description=r["description"],
                currency=r["currency"],
                po_amount=float(r["po_amount"]),
                issued_date=r["issued_date"],
                status=r["status"],
                allow_partial=r["allow_partial"].strip().lower() == "true",
                tolerance_pct=float(r["tolerance_pct"]),
            )
            for r in csv.DictReader(fh)
        ]


def resolve_vendor(name: Optional[str], tax_id: Optional[str] = None
                   ) -> tuple[Optional[Vendor], float, str]:
    """Return (vendor, confidence, how_we_matched).

    Tax ID is checked first because it is the only identifier a vendor cannot
    typo their way out of. Name matching is a fallback, and we report how
    confident it was rather than silently accepting a near-miss.
    """
    vendors = load_vendors()

    if tax_id:
        clean = re.sub(r"[^A-Za-z0-9]", "", tax_id).upper()
        for v in vendors:
            if clean and re.sub(r"[^A-Za-z0-9]", "", v.tax_id).upper() == clean:
                return v, 1.0, "tax_id exact"

    if not name:
        return None, 0.0, "no vendor name extracted"

    target = _norm(name)
    best, best_score, how = None, 0.0, ""
    for v in vendors:
        for candidate in [v.legal_name, *v.aliases]:
            c = _norm(candidate)
            if not c:
                continue
            score = 1.0 if c == target else SequenceMatcher(None, target, c).ratio()
            if score > best_score:
                best, best_score, how = v, score, (
                    f"name exact ('{candidate}')" if score == 1.0
                    else f"name fuzzy {score:.0%} ('{candidate}')"
                )

    # Below 0.80 we would be inventing a relationship that isn't there.
    if best_score < 0.80:
        return None, best_score, f"no vendor above 80% similarity (best {best_score:.0%})"
    return best, best_score, how


def find_po(po_reference: Optional[str]) -> Optional[PurchaseOrder]:
    """Find a PO inside a free-text reference.

    Vendors write the reference a dozen ways, and often bury it in a sentence:
    "PO 4401", "po#4401", "Ref: PO-4401 / release 1 of 3". We deliberately do
    NOT ask the extractor to normalise it - it reports what the page says, and
    tolerating the variation is this function's job. Stripping every non-digit
    from the whole string is wrong: "PO-4404 / release 1 of 3" would become
    "440413" and match nothing.
    """
    if not po_reference:
        return None
    ref = po_reference.strip()
    for po in load_pos():
        if po.po_number.lower() == ref.lower():
            return po
    for po in load_pos():
        # The numeric core as a standalone token anywhere in the reference.
        core = re.sub(r"\D", "", po.po_number)
        if core and re.search(rf"(?<!\d){re.escape(core)}(?!\d)", ref):
            return po
    return None


def pos_for_vendor(vendor_id: str) -> list[PurchaseOrder]:
    return [p for p in load_pos() if p.vendor_id == vendor_id]
