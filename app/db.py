"""SQLite run store.

This is not just demo logging. Two rules genuinely depend on it:
  * duplicate detection needs every invoice we have ever seen, and
  * cumulative PO billing needs what has already been charged to a PO.
So the store is part of the process logic, not an afterthought.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator, Optional

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id                TEXT PRIMARY KEY,
    created_at        TEXT NOT NULL,
    completed_at      TEXT,
    source_file       TEXT NOT NULL,
    file_sha256       TEXT,
    scenario          TEXT,
    status            TEXT NOT NULL,      -- running | complete | error
    decision          TEXT,               -- AUTO_APPROVE | NEEDS_REVIEW | HOLD | REJECT
    decision_reason   TEXT,
    next_action       TEXT,
    vendor_id         TEXT,
    vendor_name       TEXT,
    invoice_number    TEXT,
    invoice_date      TEXT,
    po_number         TEXT,
    currency          TEXT,
    invoice_total     REAL,
    extraction_mode   TEXT,               -- text_layer | image_vision | heuristic
    extractor         TEXT,               -- claude_agent_sdk | heuristic
    confidence        REAL,
    duration_ms       INTEGER,
    human_action      TEXT,               -- NULL | approved | rejected
    human_note        TEXT,
    extracted_json    TEXT,
    match_json        TEXT,
    findings_json     TEXT,
    stages_json       TEXT,
    error             TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_dup ON runs (vendor_id, invoice_number);
CREATE INDEX IF NOT EXISTS idx_runs_po  ON runs (po_number);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def conn() -> Iterator[sqlite3.Connection]:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init() -> None:
    with conn() as c:
        c.executescript(SCHEMA)


def create_run(run_id: str, source_file: str, scenario: Optional[str] = None) -> None:
    with conn() as c:
        c.execute(
            "INSERT INTO runs (id, created_at, source_file, scenario, status) VALUES (?,?,?,?,'running')",
            (run_id, _now(), source_file, scenario),
        )


def update_run(run_id: str, **fields: Any) -> None:
    if not fields:
        return
    for k in ("extracted_json", "match_json", "findings_json", "stages_json"):
        if k in fields and not isinstance(fields[k], (str, type(None))):
            fields[k] = json.dumps(fields[k], default=str)
    cols = ", ".join(f"{k} = ?" for k in fields)
    with conn() as c:
        c.execute(f"UPDATE runs SET {cols} WHERE id = ?", (*fields.values(), run_id))


def finish_run(run_id: str, **fields: Any) -> None:
    fields.setdefault("status", "complete")
    update_run(run_id, completed_at=_now(), **fields)


def get_run(run_id: str) -> Optional[dict[str, Any]]:
    with conn() as c:
        row = c.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    return _hydrate(row) if row else None


def list_runs(limit: int = 200) -> list[dict[str, Any]]:
    with conn() as c:
        rows = c.execute(
            "SELECT * FROM runs ORDER BY created_at DESC, rowid DESC LIMIT ?", (limit,)
        ).fetchall()
    return [_hydrate(r) for r in rows]


def _hydrate(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    for k in ("extracted_json", "match_json", "findings_json", "stages_json"):
        if d.get(k):
            try:
                d[k.replace("_json", "")] = json.loads(d[k])
            except json.JSONDecodeError:
                d[k.replace("_json", "")] = None
        else:
            d[k.replace("_json", "")] = None
        d.pop(k, None)
    return d


# --- Queries the rules engine depends on --------------------------------

def _not_rejected(d: dict[str, Any]) -> bool:
    """An invoice we rejected never made it onto the ledger, so it can't be
    the thing a later invoice duplicates or the reason a PO is exhausted."""
    return d.get("decision") != "REJECT" and d.get("human_action") != "rejected"


def find_prior_invoices(vendor_id: Optional[str], invoice_number: Optional[str],
                        exclude_run_id: str) -> list[dict[str, Any]]:
    """Exact duplicate lookup: same vendor, same invoice number."""
    if not vendor_id or not invoice_number:
        return []
    with conn() as c:
        rows = c.execute(
            "SELECT * FROM runs WHERE vendor_id = ? AND invoice_number = ? "
            "AND id != ? AND status = 'complete'",
            (vendor_id, invoice_number.strip(), exclude_run_id),
        ).fetchall()
    return [d for d in (_hydrate(r) for r in rows) if _not_rejected(d)]


def find_similar_invoices(vendor_id: Optional[str], total: Optional[float],
                          exclude_run_id: str) -> list[dict[str, Any]]:
    """Near-duplicate lookup: same vendor and same amount, different number.
    Catches the vendor who re-issues an invoice under a new number."""
    if not vendor_id or total is None:
        return []
    with conn() as c:
        rows = c.execute(
            "SELECT * FROM runs WHERE vendor_id = ? AND id != ? AND status = 'complete' "
            "AND invoice_total IS NOT NULL AND ABS(invoice_total - ?) < 0.01",
            (vendor_id, exclude_run_id, float(total)),
        ).fetchall()
    return [d for d in (_hydrate(r) for r in rows) if _not_rejected(d)]


def po_ledger(po_number: str, exclude_run_id: str) -> dict[str, Any]:
    """What has already been charged to this PO.

    'billed' is money we have released. 'encumbered' is money sitting in a
    review queue that will most likely be released. Both count against the
    PO ceiling - ignoring the pending pile is how you over-pay a PO.
    """
    with conn() as c:
        rows = c.execute(
            "SELECT * FROM runs WHERE po_number = ? AND id != ? AND status = 'complete'",
            (po_number, exclude_run_id),
        ).fetchall()

    billed, encumbered, entries = 0.0, 0.0, []
    for d in (_hydrate(r) for r in rows):
        if not _not_rejected(d) or d.get("invoice_total") is None:
            continue
        amount = float(d["invoice_total"])
        released = d.get("decision") == "AUTO_APPROVE" or d.get("human_action") == "approved"
        if released:
            billed += amount
        else:
            encumbered += amount
        entries.append({
            "run_id": d["id"],
            "invoice_number": d.get("invoice_number"),
            "amount": amount,
            "state": "billed" if released else "encumbered",
            "decision": d.get("decision"),
        })
    return {
        "billed": round(billed, 2),
        "encumbered": round(encumbered, 2),
        "consumed": round(billed + encumbered, 2),
        "entries": entries,
    }


def sweep_stale_runs(older_than_seconds: int = 300) -> int:
    """Settle rows left at 'running' by a killed process.

    A run takes seconds, so anything still 'running' after five minutes is
    wreckage, not work in progress.
    """
    cutoff = datetime.now(timezone.utc).timestamp() - older_than_seconds
    with conn() as c:
        rows = c.execute("SELECT id, created_at FROM runs WHERE status = 'running'").fetchall()
        stale = []
        for r in rows:
            try:
                if datetime.fromisoformat(r["created_at"]).timestamp() < cutoff:
                    stale.append(r["id"])
            except ValueError:
                stale.append(r["id"])
        for rid in stale:
            c.execute(
                "UPDATE runs SET status='error', completed_at=?, "
                "error='Run interrupted before completion (server restarted).' WHERE id=?",
                (_now(), rid))
    return len(stale)


def set_human_action(run_id: str, action: str, note: str = "") -> None:
    update_run(run_id, human_action=action, human_note=note)


def stats() -> dict[str, Any]:
    with conn() as c:
        rows = c.execute(
            "SELECT decision, COUNT(*) n FROM runs WHERE status='complete' GROUP BY decision"
        ).fetchall()
        total = c.execute("SELECT COUNT(*) n FROM runs").fetchone()["n"]
        durations = [r["duration_ms"] for r in c.execute(
            "SELECT duration_ms FROM runs WHERE duration_ms IS NOT NULL "
            "AND status = 'complete' ORDER BY duration_ms").fetchall()]
    by = {r["decision"]: r["n"] for r in rows if r["decision"]}
    touchless = by.get("AUTO_APPROVE", 0)
    decided = sum(by.values()) or 1
    # Median, not mean: one slow cold-start run should not move the headline.
    median = 0
    if durations:
        mid = len(durations) // 2
        median = (durations[mid] if len(durations) % 2
                  else (durations[mid - 1] + durations[mid]) // 2)

    return {
        "total_runs": total,
        "by_decision": by,
        "touchless_rate": round(100.0 * touchless / decided, 1),
        "median_duration_ms": int(median),
    }
