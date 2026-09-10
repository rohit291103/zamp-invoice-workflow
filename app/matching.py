"""Stage 4 - tie the invoice to a purchase order.

Two paths. If the invoice names a PO we verify that PO. If it does not, we try
to infer one - but only commit to an inference when exactly one candidate
survives, because a confidently wrong PO match is worse than no match at all.
"""
from __future__ import annotations

from typing import Any, Optional

from . import db
from .config import TOLERANCE_ABS, TOLERANCE_PCT
from .masterdata import find_po, pos_for_vendor
from .models import ExtractedInvoice, PurchaseOrder, Vendor


def tolerance_for(po: PurchaseOrder) -> float:
    """The greater of the percentage band and the absolute floor. Small POs
    need the floor (2% of $80 is meaningless); large POs need the percentage."""
    return max(po.po_amount * (po.tolerance_pct or TOLERANCE_PCT) / 100.0, TOLERANCE_ABS)


def _infer_po(vendor: Vendor, total: Optional[float], run_id: str
              ) -> tuple[Optional[PurchaseOrder], list[dict[str, Any]]]:
    """Guess the PO from vendor + amount when the invoice omits the reference."""
    candidates = []
    for po in pos_for_vendor(vendor.vendor_id):
        if po.status != "open" or total is None:
            continue
        ledger = db.po_ledger(po.po_number, run_id)
        remaining = po.po_amount - ledger["consumed"]
        tol = tolerance_for(po)
        # An invoice plausibly belongs to a PO if it matches the full PO value
        # or the balance still outstanding on it.
        fits_full = abs(total - po.po_amount) <= tol
        fits_remaining = po.allow_partial and total <= remaining + tol
        if fits_full or fits_remaining:
            candidates.append({
                "po_number": po.po_number,
                "po_amount": po.po_amount,
                "remaining": round(remaining, 2),
                "basis": "matches PO total" if fits_full else "fits remaining balance",
                "_po": po,
            })

    if len(candidates) == 1:
        return candidates[0]["_po"], candidates
    return None, candidates


def match(inv: ExtractedInvoice, vendor: Optional[Vendor], run_id: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "po_number": None, "method": "none", "confidence": 0.0,
        "candidates": [], "ledger": None, "variance": None, "po": None,
    }
    if vendor is None:
        result["note"] = "vendor unresolved, cannot search POs"
        return result

    po = find_po(inv.po_reference)
    if po is not None:
        result["method"] = "explicit_reference"
        result["confidence"] = 1.0
    else:
        if inv.po_reference:
            # They quoted a PO number and it isn't one of ours. That is a
            # different failure from quoting nothing at all.
            result["note"] = f"invoice cites '{inv.po_reference}' which is not a known PO"
        po, candidates = _infer_po(vendor, inv.invoice_total, run_id)
        result["candidates"] = [
            {k: v for k, v in c.items() if k != "_po"} for c in candidates
        ]
        if po is not None:
            result["method"] = "inferred_from_vendor_and_amount"
            result["confidence"] = 0.70
        elif len(candidates) > 1:
            result["note"] = (
                f"{len(candidates)} open POs for this vendor could fit this amount - "
                "ambiguous, refusing to pick one"
            )

    if po is None:
        return result

    ledger = db.po_ledger(po.po_number, run_id)
    tol = tolerance_for(po)
    total = inv.invoice_total or 0.0
    cumulative = ledger["consumed"] + total

    # For a partial-billing PO the ceiling applies to everything charged to
    # date, not to this one invoice. That distinction is the whole point.
    compare_to, basis = (
        (cumulative, "cumulative") if po.allow_partial else (total, "single_invoice")
    )
    variance_abs = round(compare_to - po.po_amount, 2)

    result.update({
        "po_number": po.po_number,
        "po": {
            "po_number": po.po_number, "description": po.description,
            "currency": po.currency, "po_amount": po.po_amount,
            "status": po.status, "allow_partial": po.allow_partial,
            "vendor_id": po.vendor_id,
        },
        "ledger": ledger,
        "variance": {
            "basis": basis,
            "invoice_total": round(total, 2),
            "already_consumed": ledger["consumed"],
            "compared_amount": round(compare_to, 2),
            "po_amount": po.po_amount,
            "variance_abs": variance_abs,
            "variance_pct": round(100.0 * variance_abs / po.po_amount, 2) if po.po_amount else 0.0,
            "tolerance_abs": round(tol, 2),
            "within_tolerance": variance_abs <= tol,
            "remaining_after": round(po.po_amount - cumulative, 2),
        },
    })
    return result
