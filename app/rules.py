"""Stage 5 - the rules engine, and stage 6 - the decision.

Design intent: every rule is small, named, independently testable, and emits a
message an AP clerk could act on without reading code. The decision is a pure
function of the findings, so the audit trail always explains the outcome - you
can never get an approval whose reasons you cannot enumerate.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Callable, Optional

from . import db
from .config import (AUTO_APPROVE_CONFIDENCE, CRITICAL_FIELDS, HARD_VARIANCE_PCT,
                     NEAR_DUPLICATE_WINDOW_DAYS, REQUIRE_TEXT_LAYER_FOR_AUTO_APPROVE)
from .models import ExtractedInvoice, Finding, Vendor

# Worst outcome wins.
DECISION_RANK = {"AUTO_APPROVE": 0, "NEEDS_REVIEW": 1, "HOLD": 2, "REJECT": 3}

DECISION_MEANING = {
    "AUTO_APPROVE": "Released for payment with no human touch.",
    "NEEDS_REVIEW": "Payable, but a human signs off first.",
    "HOLD": "Cannot be decided with what we have. Something must come back from the vendor or procurement.",
    "REJECT": "Must not be paid.",
}


class Ctx:
    """Everything the rules are allowed to look at."""

    def __init__(self, inv: ExtractedInvoice, vendor: Optional[Vendor],
                 vendor_match: dict[str, Any], match: dict[str, Any],
                 ingest: dict[str, Any], run_id: str):
        self.inv = inv
        self.vendor = vendor
        self.vendor_match = vendor_match
        self.match = match
        self.ingest = ingest
        self.run_id = run_id


def _parse_date(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.strip()[:10]).date()
    except ValueError:
        return None


# --------------------------------------------------------------------------
# Rules. Each returns a Finding or None.
# --------------------------------------------------------------------------

def r01_vendor_resolved(c: Ctx) -> Optional[Finding]:
    if c.vendor is not None:
        return Finding("R01", "Vendor resolved", "info",
                       f"Matched to {c.vendor.legal_name} ({c.vendor.vendor_id}) by "
                       f"{c.vendor_match.get('how')}.",
                       {"vendor_id": c.vendor.vendor_id,
                        "score": round(c.vendor_match.get("score", 0), 3)})
    return Finding("R01", "Vendor resolved", "block",
                   f"'{c.inv.vendor_name or 'unknown'}' does not match any vendor in the "
                   f"master file. {c.vendor_match.get('how')}.",
                   {"extracted_name": c.inv.vendor_name}, outcome="HOLD")


def r02_vendor_status(c: Ctx) -> Optional[Finding]:
    if c.vendor is None:
        return None
    if c.vendor.status == "blocked":
        return Finding("R02", "Vendor is blocked", "block",
                       f"{c.vendor.legal_name} is blocked: {c.vendor.status_reason}. "
                       "No invoice from this vendor may be paid.",
                       {"status_reason": c.vendor.status_reason}, outcome="REJECT")
    if c.vendor.status == "on_hold":
        return Finding("R03", "Vendor is on hold", "warn",
                       f"{c.vendor.legal_name} is on hold: {c.vendor.status_reason}. "
                       "Payment needs an exception approval.",
                       {"status_reason": c.vendor.status_reason})
    return None


def r04_invoice_number(c: Ctx) -> Optional[Finding]:
    if c.inv.invoice_number:
        return None
    # Not cosmetic: the invoice number is the key duplicate detection runs on.
    # Without it we cannot promise we will not pay this twice.
    return Finding("R04", "Invoice number present", "block",
                   "No invoice number on the document. Without one we cannot guarantee "
                   "this is not paid twice, so it cannot be released. Ask the vendor to "
                   "re-issue with an invoice number.",
                   {}, outcome="HOLD")


def r05_invoice_date(c: Ctx) -> Optional[Finding]:
    d = _parse_date(c.inv.invoice_date)
    if d is None:
        return Finding("R05", "Invoice date present", "warn",
                       "No readable invoice date. Payment terms and ageing cannot be "
                       "calculated; a reviewer should confirm the date.", {})
    if d > date.today():
        return Finding("R05", "Invoice date valid", "warn",
                       f"Invoice is dated {d.isoformat()}, in the future.",
                       {"invoice_date": d.isoformat()})
    return None


def r06_total_present(c: Ctx) -> Optional[Finding]:
    if c.inv.invoice_total is None:
        return Finding("R06", "Invoice total present", "block",
                       "No payable total could be read from the document.",
                       {}, outcome="HOLD")
    if c.inv.invoice_total <= 0:
        return Finding("R06", "Invoice total valid", "block",
                       f"Total is {c.inv.invoice_total}. A zero or negative invoice is a "
                       "credit note and goes through a different process.",
                       {"invoice_total": c.inv.invoice_total}, outcome="REJECT")
    return None


def r07_po_resolved(c: Ctx) -> Optional[Finding]:
    m = c.match
    if m["method"] == "explicit_reference":
        return Finding("R07", "PO matched", "info",
                       f"Invoice cites {m['po_number']} and it was found.",
                       {"po_number": m["po_number"], "method": m["method"]})
    if m["method"] == "inferred_from_vendor_and_amount":
        return Finding("R07", "PO inferred", "warn",
                       f"No PO quoted on the invoice. Inferred {m['po_number']} as the only "
                       "open PO for this vendor that fits the amount. A reviewer should "
                       "confirm before payment.",
                       {"po_number": m["po_number"], "confidence": m["confidence"],
                        "candidates": m.get("candidates")})
    return Finding("R07", "PO matched", "block",
                   m.get("note") or "No purchase order could be matched to this invoice.",
                   {"candidates": m.get("candidates"),
                    "cited_reference": c.inv.po_reference}, outcome="HOLD")


def r08_po_open(c: Ctx) -> Optional[Finding]:
    po = c.match.get("po")
    if po and po["status"] != "open":
        return Finding("R08", "PO is open", "block",
                       f"{po['po_number']} is {po['status']}. Procurement must reopen it or "
                       "raise a new PO before this can be paid.",
                       {"po_status": po["status"]}, outcome="HOLD")
    return None


def r09_po_vendor_match(c: Ctx) -> Optional[Finding]:
    po = c.match.get("po")
    if po and c.vendor and po["vendor_id"] != c.vendor.vendor_id:
        return Finding("R09", "PO belongs to this vendor", "block",
                       f"{po['po_number']} belongs to {po['vendor_id']}, but this invoice is "
                       f"from {c.vendor.vendor_id}. Either the PO reference is wrong or this "
                       "is an attempt to bill against someone else's order.",
                       {"po_vendor": po["vendor_id"], "invoice_vendor": c.vendor.vendor_id},
                       outcome="REJECT")
    return None


def r10_currency_match(c: Ctx) -> Optional[Finding]:
    po = c.match.get("po")
    if not po or not c.inv.currency:
        return None
    if c.inv.currency.upper() != po["currency"].upper():
        return Finding("R10", "Currency matches PO", "block",
                       f"Invoice is in {c.inv.currency} but {po['po_number']} was raised in "
                       f"{po['currency']}. We will not silently apply an FX rate.",
                       {"invoice_currency": c.inv.currency, "po_currency": po["currency"]},
                       outcome="HOLD")
    return None


def r11_amount_tolerance(c: Ctx) -> Optional[Finding]:
    v = c.match.get("variance")
    if not v:
        return None
    if v["within_tolerance"]:
        if abs(v["variance_abs"]) < 0.005:
            return Finding("R11", "Amount matches PO", "info",
                           "Amount matches the purchase order exactly.", v)
        if v["variance_abs"] < 0:
            # Under the PO. On a partial-billing order that is normal; on a
            # single-invoice order it means we are being billed less than agreed.
            return Finding("R11", "Amount within tolerance", "info",
                           f"{v['compared_amount']:,.2f} against a PO of "
                           f"{v['po_amount']:,.2f} - under by "
                           f"{abs(v['variance_abs']):,.2f}"
                           + (", which is expected for partial billing."
                              if v["basis"] == "cumulative" else "."), v)
        return Finding("R11", "Amount within tolerance", "info",
                       f"{v['variance_abs']:+,.2f} against the PO "
                       f"({v['variance_pct']:+.2f}%), inside the "
                       f"{v['tolerance_abs']:,.2f} tolerance.", v)

    label = ("Cumulative billing against this PO" if v["basis"] == "cumulative"
             else "Invoice amount")
    if v["variance_pct"] > HARD_VARIANCE_PCT:
        return Finding("R11", "Amount within tolerance", "block",
                       f"{label} exceeds the PO by {v['variance_abs']:,.2f} "
                       f"({v['variance_pct']:.1f}%), well beyond the "
                       f"{HARD_VARIANCE_PCT}% limit. This is not a rounding difference - "
                       "procurement must confirm or amend the PO.", v, outcome="HOLD")
    return Finding("R11", "Amount within tolerance", "warn",
                   f"{label} exceeds the PO by {v['variance_abs']:,.2f} "
                   f"({v['variance_pct']:.2f}%), over the {v['tolerance_abs']:,.2f} "
                   "tolerance. Needs a reviewer.", v)


def r12_po_overbilling(c: Ctx) -> Optional[Finding]:
    """The split-billing guard: each invoice looks reasonable on its own, and
    together they overrun the order."""
    v, ledger, po = c.match.get("variance"), c.match.get("ledger"), c.match.get("po")
    if not (v and ledger and po and po["allow_partial"]):
        return None
    if not ledger["entries"]:
        return None
    if v["compared_amount"] > po["po_amount"] + v["tolerance_abs"]:
        prior = ", ".join(
            f"{e['invoice_number'] or e['run_id'][:8]} {e['amount']:,.2f} ({e['state']})"
            for e in ledger["entries"])
        return Finding("R12", "PO not over-billed", "block",
                       f"{po['po_number']} is for {po['po_amount']:,.2f}. Already charged: "
                       f"{ledger['consumed']:,.2f} ({prior}). This invoice of "
                       f"{v['invoice_total']:,.2f} would take the total to "
                       f"{v['compared_amount']:,.2f}, over-billing the PO by "
                       f"{v['compared_amount'] - po['po_amount']:,.2f}.",
                       {"ledger": ledger, "variance": v}, outcome="HOLD")
    return Finding("R12", "PO not over-billed", "info",
                   f"Partial billing: {v['compared_amount']:,.2f} of {po['po_amount']:,.2f} "
                   f"charged, {v['remaining_after']:,.2f} remaining on the PO.",
                   {"ledger": ledger})


def r18_po_already_invoiced(c: Ctx) -> Optional[Finding]:
    """The mirror of R12, for POs that do NOT allow partial billing.

    R12 guards the ceiling on a partial-billing PO. This guards the other case:
    a PO meant to be settled by one invoice that receives a second one. Each
    invoice can look perfect on its own - different number, correct vendor,
    amount matching the PO exactly - and together they pay twice for one order.
    Without this rule that pattern is invisible.
    """
    po, ledger = c.match.get("po"), c.match.get("ledger")
    if not (po and ledger) or po["allow_partial"] or not ledger["entries"]:
        return None
    prior = ledger["entries"][0]
    ref = prior["invoice_number"] or prior["run_id"][:8]
    return Finding("R18", "PO not already invoiced", "block",
                   f"{po['po_number']} is a single-invoice order for "
                   f"{po['po_amount']:,.2f} and has already been billed by {ref} for "
                   f"{prior['amount']:,.2f} ({prior['state']}). A second invoice against "
                   "it would pay the same order twice - procurement must confirm it or "
                   "raise a new PO.",
                   {"prior": prior, "ledger": ledger}, outcome="HOLD")


def r13_exact_duplicate(c: Ctx) -> Optional[Finding]:
    if not (c.vendor and c.inv.invoice_number):
        return None
    priors = db.find_prior_invoices(c.vendor.vendor_id, c.inv.invoice_number, c.run_id)
    if not priors:
        return None
    p = priors[0]
    return Finding("R13", "Not a duplicate", "block",
                   f"Invoice {c.inv.invoice_number} from {c.vendor.legal_name} was already "
                   f"processed on {p['created_at'][:10]} (run {p['id'][:8]}, decision "
                   f"{p['decision']}). Paying it again would be a double payment.",
                   {"prior_run_id": p["id"], "prior_decision": p["decision"],
                    "prior_total": p.get("invoice_total")}, outcome="REJECT")


def r14_near_duplicate(c: Ctx) -> Optional[Finding]:
    """Same vendor, same amount, different invoice number, close in time. Often
    innocent (a monthly retainer); occasionally a re-issued invoice being paid
    twice. Worth a human look, not a rejection."""
    if not (c.vendor and c.inv.invoice_total is not None):
        return None
    priors = db.find_similar_invoices(c.vendor.vendor_id, c.inv.invoice_total, c.run_id)
    this_date = _parse_date(c.inv.invoice_date) or date.today()
    near = []
    for p in priors:
        if p.get("invoice_number") and p["invoice_number"] == (c.inv.invoice_number or ""):
            continue  # that is R13's job
        pd = _parse_date(p.get("invoice_date")) or _parse_date(p.get("created_at"))
        if pd and abs((this_date - pd).days) <= NEAR_DUPLICATE_WINDOW_DAYS:
            near.append(p)
    if not near:
        return None
    p = near[0]
    return Finding("R14", "Not a near-duplicate", "warn",
                   f"Same vendor and same amount ({c.inv.invoice_total:,.2f}) as invoice "
                   f"{p.get('invoice_number')} within {NEAR_DUPLICATE_WINDOW_DAYS} days, under "
                   "a different invoice number. Confirm this is genuinely a second charge.",
                   {"similar_run_id": p["id"], "similar_invoice": p.get("invoice_number")})


def r15_arithmetic(c: Ctx) -> Optional[Finding]:
    i = c.inv
    if i.subtotal is None or i.invoice_total is None:
        return None
    expected = i.subtotal + (i.tax_amount or 0.0)
    if abs(expected - i.invoice_total) > 0.01:
        return Finding("R15", "Invoice arithmetic", "warn",
                       f"Subtotal {i.subtotal:,.2f} plus tax {(i.tax_amount or 0):,.2f} is "
                       f"{expected:,.2f}, but the stated total is {i.invoice_total:,.2f}. "
                       "The document does not add up.",
                       {"subtotal": i.subtotal, "tax": i.tax_amount,
                        "stated_total": i.invoice_total, "computed": round(expected, 2)})
    return None


def r16_extraction_confidence(c: Ctx) -> Optional[Finding]:
    low = {f: c.inv.confidence_for(f) for f in CRITICAL_FIELDS
           if c.inv.confidence_for(f) < AUTO_APPROVE_CONFIDENCE}
    if not low:
        return None
    detail = ", ".join(f"{k} {v:.0%}" for k, v in sorted(low.items(), key=lambda kv: kv[1]))
    return Finding("R16", "Extraction confidence", "warn",
                   f"Read with low confidence on: {detail}. A reviewer should verify these "
                   "against the document before payment.",
                   {"low_confidence_fields": low,
                    "threshold": AUTO_APPROVE_CONFIDENCE,
                    "notes": c.inv.extraction_notes})


def r17_scanned_document(c: Ctx) -> Optional[Finding]:
    """Policy, not arithmetic: money does not move on OCR'd numbers unattended."""
    if c.ingest.get("mode") != "image_vision":
        return None
    if not REQUIRE_TEXT_LAYER_FOR_AUTO_APPROVE:
        return Finding("R17", "Document is a scan", "info",
                       "Read visually from a page image.", {})
    return Finding("R17", "Document is a scan", "warn",
                   "This is a scanned image with no text layer, so every figure was "
                   "interpreted rather than read. Policy requires a human to confirm the "
                   "total and invoice number before payment.",
                   {"page_count": c.ingest.get("page_count"),
                    "char_count": c.ingest.get("char_count")})


RULES: list[Callable[[Ctx], Optional[Finding]]] = [
    r01_vendor_resolved, r02_vendor_status, r04_invoice_number, r05_invoice_date,
    r06_total_present, r07_po_resolved, r08_po_open, r09_po_vendor_match,
    r10_currency_match, r11_amount_tolerance, r12_po_overbilling,
    r18_po_already_invoiced, r13_exact_duplicate, r14_near_duplicate, r15_arithmetic,
    r16_extraction_confidence, r17_scanned_document,
]


def evaluate(ctx: Ctx) -> list[Finding]:
    findings: list[Finding] = []
    for rule in RULES:
        try:
            f = rule(ctx)
        except Exception as exc:  # a broken rule must not fail the invoice open
            f = Finding(rule.__name__[:3].upper(), rule.__name__, "warn",
                        f"Rule could not be evaluated ({exc}). Treating as needing review.",
                        {"error": str(exc)})
        if f is not None:
            findings.append(f)
    return findings


def decide(findings: list[Finding]) -> dict[str, Any]:
    """Pure function of the findings - no hidden state, always explainable."""
    decision = "AUTO_APPROVE"
    for f in findings:
        candidate = f.outcome if f.severity == "block" else (
            "NEEDS_REVIEW" if f.severity == "warn" else "AUTO_APPROVE")
        if candidate and DECISION_RANK[candidate] > DECISION_RANK[decision]:
            decision = candidate

    drivers = [f for f in findings
               if (f.severity == "block" and f.outcome == decision)
               or (decision == "NEEDS_REVIEW" and f.severity == "warn")]
    if decision == "AUTO_APPROVE":
        reason = "All checks passed: vendor approved, PO matched, amount within tolerance, no duplicate."
        next_action = "Queued for payment on vendor terms. No human action needed."
    else:
        reason = " ".join(f"[{f.rule_id}] {f.message}" for f in drivers) or "See findings."
        next_action = {
            "NEEDS_REVIEW": "Route to the AP reviewer queue with the flagged fields highlighted.",
            "HOLD": "Raise an exception to the vendor or procurement; the invoice stays unpaid until it is answered.",
            "REJECT": "Do not pay. Notify the vendor and log the rejection reason.",
        }[decision]

    return {
        "decision": decision,
        "meaning": DECISION_MEANING[decision],
        "reason": reason,
        "next_action": next_action,
        "driving_rules": [f.rule_id for f in drivers],
        "counts": {
            "block": sum(1 for f in findings if f.severity == "block"),
            "warn": sum(1 for f in findings if f.severity == "warn"),
            "info": sum(1 for f in findings if f.severity == "info"),
        },
    }
