"""Check a deployment backend against known-correct extraction.

Before pointing a public link at a model, you want two questions answered:
does it read the fields correctly, and does the workflow still reach the same
decisions? A model can be wrong in ways that never change the outcome, and
right in ways that do - so this reports both, separately.

    python scripts/compare_providers.py                  # current backend
    EXTRACTION_PROVIDER=openai_compat python scripts/compare_providers.py
    python scripts/compare_providers.py --only 02        # one scenario
"""
from __future__ import annotations

import sys
from pathlib import Path

import anyio

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db                             # noqa: E402
from app.catalogue import CATALOGUE            # noqa: E402
from app.config import INVOICE_DIR             # noqa: E402
from app.extractors import resolve             # noqa: E402
from app.pipeline import run_pipeline          # noqa: E402

C = {"r": "\033[0m", "dim": "\033[2m", "b": "\033[1m", "g": "\033[32m",
     "y": "\033[33m", "red": "\033[31m", "cy": "\033[36m"}

# What each document actually says. Hand-checked against the PDFs.
TRUTH: dict[str, dict[str, object]] = {
    "01_happy_path.pdf":              {"invoice_number": "INV-KS-8841", "po": "4403", "total": 3150.00,  "ccy": "USD"},
    "02_edge_scanned_freight.pdf":    {"invoice_number": "NW-3310",     "po": "4402", "total": 12400.00, "ccy": "EUR"},
    "03a_split_release_1.pdf":        {"invoice_number": "INV-VP-771",  "po": "4404", "total": 12000.00, "ccy": "USD"},
    "03b_split_release_2.pdf":        {"invoice_number": "INV-VP-782",  "po": "4404", "total": 10500.00, "ccy": "USD"},
    "03c_split_release_3.pdf":        {"invoice_number": "INV-VP-795",  "po": "4404", "total": 6200.00,  "ccy": "USD"},
    "04_edge_duplicate_resubmit.pdf": {"invoice_number": "INV-KS-8841", "po": "4403", "total": 3150.00,  "ccy": "USD"},
    "05_edge_no_po_no_number.pdf":    {"invoice_number": None,          "po": None,   "total": 48600.00, "ccy": "USD"},
    "09_edge_same_po_new_number.pdf": {"invoice_number": "INV-KS-8907", "po": "4403", "total": 3150.00,  "ccy": "USD"},
    "06_blocked_vendor.pdf":          {"invoice_number": "HAL-5510",    "po": "4407", "total": 41200.00, "ccy": "USD"},
    "07_tolerance_minor_overage.pdf": {"invoice_number": "ASW-2026-0442", "po": "4408", "total": 6520.00, "ccy": "USD"},
    "08_vendor_on_hold.pdf":          {"invoice_number": "BL-220",      "po": "4405", "total": 15000.00, "ccy": "USD"},
}

# The decisions the workflow reaches when extraction is correct.
EXPECTED = {
    "01_happy_path.pdf": "AUTO_APPROVE",
    "02_edge_scanned_freight.pdf": "NEEDS_REVIEW",
    "03a_split_release_1.pdf": "AUTO_APPROVE",
    "03b_split_release_2.pdf": "AUTO_APPROVE",
    "03c_split_release_3.pdf": "HOLD",
    "04_edge_duplicate_resubmit.pdf": "REJECT",
    "05_edge_no_po_no_number.pdf": "HOLD",
    "09_edge_same_po_new_number.pdf": "HOLD",
    "06_blocked_vendor.pdf": "REJECT",
    "07_tolerance_minor_overage.pdf": "AUTO_APPROVE",
    "08_vendor_on_hold.pdf": "NEEDS_REVIEW",
}


def c(t: str, k: str) -> str:
    return f"{C[k]}{t}{C['r']}"


def check(inv: dict, truth: dict) -> tuple[int, int, list[str]]:
    """Compare extracted fields with the document. Returns hits, total, notes."""
    notes, hits, total = [], 0, 0

    total += 1
    got, want = inv.get("invoice_number"), truth["invoice_number"]
    if (got or None) == want:
        hits += 1
    else:
        notes.append(f"invoice_number: got {got!r}, expected {want!r}")

    total += 1
    got_po = inv.get("po_reference") or ""
    want_po = truth["po"]
    if want_po is None:
        ok = not got_po
    else:
        ok = want_po in "".join(ch for ch in got_po if ch.isdigit())
    if ok:
        hits += 1
    else:
        notes.append(f"po_reference: got {got_po!r}, expected one containing {want_po!r}")

    total += 1
    got_t = inv.get("invoice_total")
    if got_t is not None and abs(float(got_t) - float(truth["total"])) < 0.01:
        hits += 1
    else:
        notes.append(f"invoice_total: got {got_t!r}, expected {truth['total']}")

    total += 1
    if (inv.get("currency") or "").upper() == truth["ccy"]:
        hits += 1
    else:
        notes.append(f"currency: got {inv.get('currency')!r}, expected {truth['ccy']!r}")

    return hits, total, notes


async def main() -> None:
    args = sys.argv[1:]
    only = args[args.index("--only") + 1] if "--only" in args else None

    backend = resolve()
    from app import config
    print(f"\n{c('Backend', 'b')}: {backend.name}")
    if backend.name == "openai_compatible":
        print(f"  text   {config.OPENAI_COMPAT_MODEL} @ {config.OPENAI_COMPAT_BASE_URL}")
        print(f"  vision {config.OPENAI_COMPAT_VISION_MODEL} @ {config.OPENAI_COMPAT_VISION_BASE_URL}")
    print()

    db.init()
    from app.config import DB_PATH
    DB_PATH.unlink(missing_ok=True)          # order-dependent rules need a clean slate
    db.init()

    field_hits = field_total = 0
    decision_ok = decision_total = 0
    degraded: list[str] = []

    for name in EXPECTED:
        if only and not name.startswith(only):
            continue
        path = INVOICE_DIR / name
        inv, decision, fell_back = {}, None, False

        async for ev in run_pipeline(path, scenario=CATALOGUE.get(name, {}).get("title")):
            if ev["type"] == "stage":
                if ev["status"] == "degraded":
                    fell_back = True
                if ev["stage"] == "normalize" and ev["status"] == "done":
                    inv = (ev.get("data") or {}).get("invoice", {}) or {}
            elif ev["type"] == "run_complete":
                decision = ev["decision"]["decision"]
            elif ev["type"] == "run_failed":
                decision = "ERROR"

        hits, tot, notes = check(inv, TRUTH[name])
        field_hits += hits
        field_total += tot
        want = EXPECTED[name]
        decision_total += 1
        match = decision == want
        decision_ok += match

        mark = c("PASS", "g") if match else c("FAIL", "red")
        fields = f"{hits}/{tot}"
        fcol = "g" if hits == tot else ("y" if hits >= tot - 1 else "red")
        line = f"  {mark}  {name:34s} fields {c(fields, fcol)}  {decision or '—'}"
        if not match:
            line += c(f"  (expected {want})", "red")
        if fell_back:
            line += c("  [fell back to regex]", "y")
            degraded.append(name)
        print(line)
        for n in notes:
            print(f"         {c(n, 'dim')}")

    print()
    fp = 100 * field_hits / max(field_total, 1)
    dp = 100 * decision_ok / max(decision_total, 1)
    print(f"  {c('Field accuracy', 'b')}    {field_hits}/{field_total}  ({fp:.0f}%)")
    print(f"  {c('Decisions correct', 'b')} {decision_ok}/{decision_total}  ({dp:.0f}%)")
    if degraded:
        print(c(f"  {len(degraded)} run(s) fell back to the regex extractor: "
                + ", ".join(degraded), "y"))
    print()
    if dp < 100:
        print(c("  Not safe to deploy behind a public link yet.", "red"))
    elif fp < 100:
        print(c("  Decisions all correct, but extraction is imperfect — "
                "check the field notes above.", "y"))
    else:
        print(c("  Matches the reference behaviour exactly.", "g"))
    print()


if __name__ == "__main__":
    anyio.run(main)
