"""The rules engine and the decision function.

These run with no model calls, so the whole judgment layer is verifiable in
milliseconds - the model's job is extraction, and extraction is the only part
that needs the network.
"""
from __future__ import annotations

import pytest

from app import rules
from app.masterdata import load_pos, load_vendors
from app.matching import match
from app.models import ExtractedInvoice, Finding

VENDOR = {v.vendor_id: v for v in load_vendors()}
PO = {p.po_number: p for p in load_pos()}


def make_ctx(inv, vendor=None, run_id="run-under-test", ingest=None):
    m = match(inv, vendor, run_id)
    return rules.Ctx(inv, vendor, {"score": 1.0, "how": "tax_id exact"}, m,
                     ingest or {"mode": "text_layer", "page_count": 1, "char_count": 500},
                     run_id)


def invoice(**kw):
    base = dict(vendor_name="Kestrel Office Supplies LLC", invoice_number="INV-1",
                invoice_date="2026-08-28", po_reference="PO-4403", currency="USD",
                invoice_total=3150.00,
                field_confidence={k: 1.0 for k in
                                  ("vendor_name", "invoice_number", "invoice_total", "currency")})
    base.update(kw)
    return ExtractedInvoice(**base)


def ids(findings):
    return {f.rule_id for f in findings}


def by_id(findings, rid):
    return next(f for f in findings if f.rule_id == rid)


# --- the decision function is pure ---------------------------------------

def test_worst_finding_wins():
    assert rules.decide([Finding("A", "a", "info", "")])["decision"] == "AUTO_APPROVE"
    assert rules.decide([Finding("A", "a", "warn", "")])["decision"] == "NEEDS_REVIEW"
    assert rules.decide([
        Finding("A", "a", "warn", ""),
        Finding("B", "b", "block", "", outcome="HOLD"),
    ])["decision"] == "HOLD"
    # REJECT outranks HOLD even when the HOLD is found first.
    assert rules.decide([
        Finding("B", "b", "block", "", outcome="HOLD"),
        Finding("C", "c", "block", "", outcome="REJECT"),
    ])["decision"] == "REJECT"


def test_every_non_approval_names_the_rules_that_caused_it():
    d = rules.decide([Finding("R99", "x", "block", "because reasons", outcome="HOLD")])
    assert d["driving_rules"] == ["R99"]
    assert "because reasons" in d["reason"]
    assert d["next_action"]           # an operator is always told what to do next


# --- happy path -----------------------------------------------------------

def test_clean_invoice_auto_approves():
    f = rules.evaluate(make_ctx(invoice(), VENDOR["V-1003"]))
    assert rules.decide(f)["decision"] == "AUTO_APPROVE"
    assert not [x for x in f if x.severity in ("warn", "block")]


# --- vendor rules ---------------------------------------------------------

def test_blocked_vendor_is_rejected_outright():
    inv = invoice(vendor_name="Halcyon IT Services", po_reference="PO-4407",
                  invoice_total=41200.00)
    f = rules.evaluate(make_ctx(inv, VENDOR["V-1006"]))
    assert by_id(f, "R02").outcome == "REJECT"
    assert rules.decide(f)["decision"] == "REJECT"


def test_on_hold_vendor_only_needs_review():
    inv = invoice(vendor_name="Brightline Consulting Group", po_reference="PO-4405",
                  invoice_total=15000.00)
    f = rules.evaluate(make_ctx(inv, VENDOR["V-1004"]))
    assert rules.decide(f)["decision"] == "NEEDS_REVIEW"


def test_unresolvable_vendor_holds():
    f = rules.evaluate(make_ctx(invoice(vendor_name="Who Knows Ltd"), None))
    assert by_id(f, "R01").outcome == "HOLD"


# --- missing identifiers (edge case 4) ------------------------------------

def test_missing_invoice_number_blocks_even_when_everything_else_is_perfect():
    """The invoice number is the duplicate-detection key. Without it we cannot
    promise the invoice will not be paid twice, so it cannot be released."""
    f = rules.evaluate(make_ctx(invoice(invoice_number=None), VENDOR["V-1003"]))
    assert by_id(f, "R04").outcome == "HOLD"
    assert rules.decide(f)["decision"] == "HOLD"


def test_missing_po_is_inferred_but_only_flagged_not_trusted():
    inv = invoice(vendor_name="Acme Steel Works Pvt Ltd", po_reference=None,
                  invoice_total=48600.00)
    ctx = make_ctx(inv, VENDOR["V-1001"])
    assert ctx.match["po_number"] == "PO-4401"
    assert ctx.match["method"] == "inferred_from_vendor_and_amount"
    assert by_id(rules.evaluate(ctx), "R07").severity == "warn"


def test_missing_total_holds():
    f = rules.evaluate(make_ctx(invoice(invoice_total=None), VENDOR["V-1003"]))
    assert by_id(f, "R06").outcome == "HOLD"


# --- tolerance ------------------------------------------------------------

@pytest.mark.parametrize("total,expected", [
    (6400.00, "AUTO_APPROVE"),   # exact
    (6520.00, "AUTO_APPROVE"),   # +120, inside the 128 band
    (6600.00, "NEEDS_REVIEW"),   # +200, over the band but under 10%
    (7500.00, "HOLD"),           # +17%, not a rounding difference
])
def test_variance_bands(total, expected):
    inv = invoice(vendor_name="Acme Steel Works Pvt Ltd", po_reference="PO-4408",
                  invoice_total=total)
    f = rules.evaluate(make_ctx(inv, VENDOR["V-1001"]))
    assert rules.decide(f)["decision"] == expected


def test_currency_mismatch_is_never_silently_converted():
    inv = invoice(vendor_name="Northwind Logistics BV", po_reference="PO-4402",
                  currency="USD", invoice_total=12400.00)
    f = rules.evaluate(make_ctx(inv, VENDOR["V-1002"]))
    assert by_id(f, "R10").outcome == "HOLD"


def test_po_belonging_to_another_vendor_is_rejected():
    inv = invoice(vendor_name="Acme Steel Works Pvt Ltd", po_reference="PO-4403",
                  invoice_total=3150.00)
    f = rules.evaluate(make_ctx(inv, VENDOR["V-1001"]))
    assert by_id(f, "R09").outcome == "REJECT"


def test_closed_po_holds():
    inv = invoice(po_reference="PO-4406", invoice_total=890.00)
    f = rules.evaluate(make_ctx(inv, VENDOR["V-1003"]))
    assert by_id(f, "R08").outcome == "HOLD"


# --- history-dependent rules ---------------------------------------------

def test_exact_duplicate_is_rejected(booked):
    booked("V-1003", "INV-KS-8841", "PO-4403", 3150.00)
    f = rules.evaluate(make_ctx(invoice(invoice_number="INV-KS-8841"), VENDOR["V-1003"]))
    assert by_id(f, "R13").outcome == "REJECT"


def test_a_rejected_invoice_does_not_block_a_legitimate_resubmission(booked):
    """If we rejected it, it never went on the ledger - so the corrected
    invoice that follows must not be treated as a duplicate of it."""
    booked("V-1003", "INV-KS-8841", "PO-4403", 3150.00, decision="REJECT")
    f = rules.evaluate(make_ctx(invoice(invoice_number="INV-KS-8841"), VENDOR["V-1003"]))
    assert "R13" not in ids(f)


def test_split_billing_accumulates_then_blocks(booked):
    """Edge case 2: each invoice is fine alone; only the running total catches it."""
    inv = invoice(vendor_name="Vertex Packaging Co", po_reference="PO-4404",
                  invoice_number="INV-VP-795", invoice_total=6200.00)

    booked("V-1005", "INV-VP-771", "PO-4404", 12000.00)
    # 18,200 of 27,000 - R12 reports the running total but does not object.
    f = rules.evaluate(make_ctx(inv, VENDOR["V-1005"]))
    assert by_id(f, "R12").severity == "info"
    assert rules.decide(f)["decision"] == "AUTO_APPROVE"

    booked("V-1005", "INV-VP-782", "PO-4404", 10500.00)
    f = rules.evaluate(make_ctx(inv, VENDOR["V-1005"]))                       # 28,700 of 27,000
    assert by_id(f, "R12").outcome == "HOLD"
    assert rules.decide(f)["decision"] == "HOLD"


def test_second_invoice_on_a_single_invoice_po_is_caught(booked):
    """Edge case 5: a fresh invoice number and an exact amount, so neither the
    duplicate rule nor the tolerance rule sees anything wrong."""
    booked("V-1003", "INV-KS-8841", "PO-4403", 3150.00)
    f = rules.evaluate(make_ctx(invoice(invoice_number="INV-KS-8907"), VENDOR["V-1003"]))
    assert "R13" not in ids(f)                     # genuinely not a duplicate
    assert by_id(f, "R11").severity == "info"      # amount is exactly on the PO
    assert by_id(f, "R18").outcome == "HOLD"       # only the PO-level check sees it


def test_near_duplicate_warns_without_blocking(booked):
    booked("V-1003", "INV-KS-0001", "PO-4403", 3150.00, invoice_date="2026-08-20")
    f = rules.evaluate(make_ctx(invoice(invoice_number="INV-KS-0002"), VENDOR["V-1003"]))
    assert by_id(f, "R14").severity == "warn"


def test_pending_invoices_still_consume_their_po(booked):
    """Money sitting in a review queue is money that will most likely be paid.
    Ignoring it is how a PO gets over-committed."""
    booked("V-1005", "INV-VP-771", "PO-4404", 26000.00, decision="HOLD")
    inv = invoice(vendor_name="Vertex Packaging Co", po_reference="PO-4404",
                  invoice_number="INV-VP-999", invoice_total=5000.00)
    f = rules.evaluate(make_ctx(inv, VENDOR["V-1005"]))
    assert by_id(f, "R12").outcome == "HOLD"


# --- extraction-quality rules --------------------------------------------

def test_a_scan_never_auto_approves():
    """Policy, not arithmetic: figures interpreted from pixels get a human."""
    ctx = make_ctx(invoice(), VENDOR["V-1003"],
                   ingest={"mode": "image_vision", "page_count": 1, "char_count": 0})
    f = rules.evaluate(ctx)
    assert by_id(f, "R17").severity == "warn"
    assert rules.decide(f)["decision"] == "NEEDS_REVIEW"


def test_low_confidence_fields_are_surfaced():
    inv = invoice(field_confidence={"vendor_name": 1.0, "invoice_number": 0.55,
                                    "invoice_total": 0.9, "currency": 1.0})
    f = rules.evaluate(make_ctx(inv, VENDOR["V-1003"]))
    assert "invoice_number" in by_id(f, "R16").evidence["low_confidence_fields"]


def test_arithmetic_that_does_not_add_up_is_flagged():
    inv = invoice(subtotal=3000.00, tax_amount=100.00, invoice_total=3150.00)
    f = rules.evaluate(make_ctx(inv, VENDOR["V-1003"]))
    assert by_id(f, "R15").severity == "warn"


def test_a_broken_rule_fails_safe(monkeypatch):
    """A rule that throws must not let the invoice through unexamined."""
    def exploding(_ctx):
        raise RuntimeError("boom")
    monkeypatch.setattr(rules, "RULES", [*rules.RULES, exploding])
    f = rules.evaluate(make_ctx(invoice(), VENDOR["V-1003"]))
    assert rules.decide(f)["decision"] == "NEEDS_REVIEW"
