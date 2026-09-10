"""The process itself: seven stages, each emitting an event as it runs.

Written as an async generator so the same code drives the live run view (over
SSE), the CLI runner, and the tests. There is exactly one implementation of
the process - the UI is a view over it, never a re-implementation.
"""
from __future__ import annotations

import re
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Optional

from . import db, rules
from .config import SCANNED_CONFIDENCE_CEILING
from .extractors import heuristic, resolve as resolve_backend
from .extractors.ingest import ingest as ingest_file
from .masterdata import resolve_vendor
from .models import ExtractedInvoice, Finding

STAGES = [
    ("ingest",    "Ingest",          "Read the file, decide if it is machine-readable or a scan"),
    ("extract",   "Extract",         "Pull invoice fields out of the document"),
    ("normalize", "Normalise",       "Clean amounts, dates and codes into canonical form"),
    ("identify",  "Identify vendor", "Resolve the letterhead to a vendor in the master file"),
    ("match",     "Match PO",        "Find and verify the purchase order"),
    ("validate",  "Validate",        "Run the rule set and collect findings"),
    ("decide",    "Decide",          "Turn the findings into one decision and a next action"),
]

CURRENCY_SYMBOLS = {"$": "USD", "€": "EUR", "£": "GBP", "₹": "INR", "rs": "INR", "usd": "USD"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _event(stage: str, status: str, detail: str = "", **data: Any) -> dict[str, Any]:
    return {"type": "stage", "stage": stage, "status": status,
            "detail": detail, "ts": _now(), "data": data or None}


def _clean_money(v: Any) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return round(float(v), 2)
    s = str(v).strip()
    neg = s.startswith("(") and s.endswith(")")
    s = re.sub(r"[^0-9.\-]", "", s)
    try:
        n = float(s)
    except ValueError:
        return None
    return round(-n if neg else n, 2)


def _clean_currency(v: Any, text: str = "") -> Optional[str]:
    if v:
        s = str(v).strip()
        if re.fullmatch(r"[A-Za-z]{3}", s):
            return s.upper()
        if s.lower() in CURRENCY_SYMBOLS:
            return CURRENCY_SYMBOLS[s.lower()]
        if s and s[0] in CURRENCY_SYMBOLS:
            return CURRENCY_SYMBOLS[s[0]]
    for sym, code in CURRENCY_SYMBOLS.items():
        if sym in text:
            return code
    return None


def _clean_date(v: Any) -> Optional[str]:
    if not v:
        return None
    s = str(v).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%b-%Y", "%d %b %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(s[:11].strip(), fmt).date().isoformat()
        except ValueError:
            continue
    m = re.search(r"\d{4}-\d{2}-\d{2}", s)
    return m.group(0) if m else None


def normalize(raw: dict[str, Any], source_text: str) -> tuple[ExtractedInvoice, list[str]]:
    """Canonicalise the model's output. Every change is recorded so the run
    view can show what was cleaned rather than silently rewriting the data."""
    notes: list[str] = []
    inv = ExtractedInvoice(
        vendor_name=(raw.get("vendor_name") or None),
        vendor_tax_id=(raw.get("vendor_tax_id") or None),
        invoice_number=(str(raw["invoice_number"]).strip() if raw.get("invoice_number") else None),
        invoice_date=_clean_date(raw.get("invoice_date")),
        po_reference=(str(raw["po_reference"]).strip() if raw.get("po_reference") else None),
        currency=_clean_currency(raw.get("currency"), source_text),
        subtotal=_clean_money(raw.get("subtotal")),
        tax_amount=_clean_money(raw.get("tax_amount")),
        invoice_total=_clean_money(raw.get("invoice_total")),
        line_items=raw.get("line_items") or [],
        field_confidence={k: float(v) for k, v in (raw.get("field_confidence") or {}).items()},
        extraction_notes=raw.get("extraction_notes"),
    )
    if raw.get("invoice_date") and not inv.invoice_date:
        notes.append(f"Could not parse date '{raw['invoice_date']}' - treated as missing.")
    if raw.get("currency") and inv.currency != raw.get("currency"):
        notes.append(f"Currency '{raw['currency']}' normalised to {inv.currency}.")

    # Tax embedded in line items rather than stated separately: recover it so
    # the arithmetic rule has something real to check.
    if inv.subtotal is None and inv.line_items:
        s = sum(_clean_money(li.get("amount")) or 0.0 for li in inv.line_items)
        if s > 0:
            inv.subtotal = round(s, 2)
            notes.append(f"Subtotal not stated - derived {s:,.2f} from line items.")
    if (inv.tax_amount is None and inv.subtotal is not None
            and inv.invoice_total is not None and inv.invoice_total > inv.subtotal):
        inv.tax_amount = round(inv.invoice_total - inv.subtotal, 2)
        notes.append(f"Tax not stated separately - derived {inv.tax_amount:,.2f}.")
    return inv, notes


async def run_pipeline(path: Path, run_id: Optional[str] = None,
                       scenario: Optional[str] = None,
                       force_heuristic: bool = False) -> AsyncIterator[dict[str, Any]]:
    run_id = run_id or uuid.uuid4().hex
    started = time.time()
    db.create_run(run_id, path.name, scenario)
    stage_log: list[dict[str, Any]] = []
    settled = False   # set once the run reaches a terminal state

    def emit(ev: dict[str, Any]) -> dict[str, Any]:
        stage_log.append(ev)
        return ev

    yield emit({"type": "run_started", "run_id": run_id, "file": path.name,
                "scenario": scenario, "stages": [
                    {"key": k, "label": l, "description": d} for k, l, d in STAGES],
                "ts": _now()})

    try:
        # 1 - INGEST ------------------------------------------------------
        yield emit(_event("ingest", "started", f"Opening {path.name}"))
        ing = ingest_file(path)
        yield emit(_event(
            "ingest", "done",
            ("Machine-readable PDF: {c} characters of text across {p} page(s)."
             if ing["has_text_layer"] else
             "No text layer found ({c} characters across {p} page(s)) - this is a scan. "
             "Switching to the vision path.").format(c=ing["char_count"], p=ing["page_count"]),
            mode=ing["mode"], page_count=ing["page_count"],
            char_count=ing["char_count"], sha256=ing["file_sha256"][:16]))
        db.update_run(run_id, file_sha256=ing["file_sha256"], extraction_mode=ing["mode"])

        # 2 - EXTRACT -----------------------------------------------------
        backend = resolve_backend()
        extractor = backend.name
        reader = "Claude" if backend.name == "claude_agent_sdk" else "the model"
        yield emit(_event(
            "extract", "started",
            f"Reading page image with {reader} (vision)" if ing["mode"] == "image_vision"
            else f"Reading text layer with {reader}"))
        try:
            if force_heuristic:
                raise RuntimeError("forced heuristic mode")
            if ing["mode"] == "image_vision":
                raw = await backend.image(ing["page_png_b64"])
            else:
                raw = await backend.text(ing["text"])
        except Exception as exc:
            # Degrade rather than fail. The findings will show the cost.
            extractor = "heuristic"
            yield emit(_event("extract", "degraded",
                              f"Model path unavailable ({exc}). Falling back to the "
                              "deterministic regex extractor.", error=str(exc)))
            raw = heuristic.extract_from_text(ing["text"])

        if ing["mode"] == "image_vision":
            # However sure the model sounds about a scan, cap it. Confidence in
            # pixels is not confidence in characters.
            raw["field_confidence"] = {
                k: min(float(v), SCANNED_CONFIDENCE_CEILING)
                for k, v in (raw.get("field_confidence") or {}).items()}

        yield emit(_event("extract", "done",
                          f"Extracted {sum(1 for k in ('vendor_name','invoice_number','invoice_total','po_reference') if raw.get(k))}/4 key fields.",
                          extractor=extractor, usage=raw.get("_usage"),
                          fields={k: raw.get(k) for k in
                                  ("vendor_name", "invoice_number", "po_reference",
                                   "currency", "invoice_total")}))

        # 3 - NORMALIZE ---------------------------------------------------
        yield emit(_event("normalize", "started", "Canonicalising amounts, dates and codes"))
        inv, norm_notes = normalize(raw, ing["text"])
        yield emit(_event("normalize", "done",
                          "; ".join(norm_notes) if norm_notes else "Nothing needed cleaning.",
                          invoice=inv.to_dict(), notes=norm_notes))
        db.update_run(run_id, extracted_json=inv.to_dict(), extractor=extractor,
                      invoice_number=inv.invoice_number, invoice_date=inv.invoice_date,
                      currency=inv.currency, invoice_total=inv.invoice_total)

        # 4 - IDENTIFY VENDOR ---------------------------------------------
        yield emit(_event("identify", "started",
                          f"Resolving '{inv.vendor_name or 'unknown'}'"))
        vendor, score, how = resolve_vendor(inv.vendor_name, inv.vendor_tax_id)
        vendor_match = {"score": score, "how": how}
        yield emit(_event("identify", "done" if vendor else "failed",
                          (f"{vendor.legal_name} ({vendor.vendor_id}), status "
                           f"{vendor.status} - matched by {how}.") if vendor
                          else f"No vendor matched: {how}.",
                          vendor_id=vendor.vendor_id if vendor else None,
                          vendor_status=vendor.status if vendor else None, how=how))
        if vendor:
            db.update_run(run_id, vendor_id=vendor.vendor_id, vendor_name=vendor.legal_name)

        # 5 - MATCH PO ----------------------------------------------------
        yield emit(_event("match", "started",
                          f"Looking up {inv.po_reference}" if inv.po_reference
                          else "No PO quoted - attempting to infer one"))
        from .matching import match as match_po
        m = match_po(inv, vendor, run_id)
        yield emit(_event("match", "done" if m["po_number"] else "failed",
                          (f"{m['po_number']} matched by {m['method'].replace('_',' ')}."
                           + (f" {m['variance']['compared_amount']:,.2f} against a PO of "
                              f"{m['po']['po_amount']:,.2f}." if m.get("variance") else ""))
                          if m["po_number"] else (m.get("note") or "No PO matched."),
                          **{k: m[k] for k in ("po_number", "method", "confidence",
                                               "candidates", "ledger", "variance", "po")}))
        if m["po_number"]:
            db.update_run(run_id, po_number=m["po_number"])

        # 6 - VALIDATE ----------------------------------------------------
        yield emit(_event("validate", "started", f"Running {len(rules.RULES)} rules"))
        ctx = rules.Ctx(inv, vendor, vendor_match, m, ing, run_id)
        findings = rules.evaluate(ctx)
        blocks = sum(1 for f in findings if f.severity == "block")
        warns = sum(1 for f in findings if f.severity == "warn")
        yield emit(_event("validate", "done",
                          f"Ran {len(rules.RULES)} checks against the vendor master, the "
                          f"purchase order and everything processed before now.",
                          findings=[f.to_dict() for f in findings]))

        # 7 - DECIDE ------------------------------------------------------
        yield emit(_event("decide", "started", "Aggregating findings"))
        decision = rules.decide(findings)
        duration_ms = int((time.time() - started) * 1000)
        # Short here; the decision card below carries the full reasoning.
        driver_count = len(decision["driving_rules"])
        yield emit(_event(
            "decide", "done",
            decision["meaning"] if decision["decision"] == "AUTO_APPROVE"
            else f"{decision['meaning']} {driver_count} finding"
                 f"{'' if driver_count == 1 else 's'} decided it.",
            **decision))

        conf = min([inv.confidence_for(f) for f in
                    ("vendor_name", "invoice_number", "invoice_total")] or [1.0])
        db.finish_run(
            run_id, decision=decision["decision"], decision_reason=decision["reason"],
            next_action=decision["next_action"], confidence=conf, duration_ms=duration_ms,
            match_json=m, findings_json=[f.to_dict() for f in findings],
            stages_json=stage_log)

        settled = True
        yield {"type": "run_complete", "run_id": run_id, "duration_ms": duration_ms,
               "decision": decision, "confidence": conf, "ts": _now()}

    except Exception as exc:
        settled = True
        db.finish_run(run_id, status="error", error=f"{exc}\n{traceback.format_exc()}",
                      duration_ms=int((time.time() - started) * 1000),
                      stages_json=stage_log)
        yield {"type": "run_failed", "run_id": run_id, "error": str(exc), "ts": _now()}

    finally:
        # A browser that navigates away mid-run closes this generator, which
        # raises GeneratorExit - not an Exception, so the handler above never
        # sees it. Without this the row would sit at 'running' forever and
        # quietly skew every dashboard number.
        if not settled:
            db.finish_run(run_id, status="error",
                          error="Run interrupted before completion (client disconnected).",
                          duration_ms=int((time.time() - started) * 1000),
                          stages_json=stage_log)
