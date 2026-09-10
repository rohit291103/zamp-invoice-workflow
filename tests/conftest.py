"""Test fixtures.

Every test runs against a throwaway SQLite file so the rules that depend on
history (duplicates, PO consumption) can be driven deterministically without
touching the demo database.
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db  # noqa: E402


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    # db.py binds DB_PATH at import time, so patch it on the module itself.
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    db.init()
    yield db


@pytest.fixture
def booked(temp_db):
    """Record a completed run, as if an invoice had already been processed."""
    def _book(vendor_id, invoice_number, po_number, total,
              decision="AUTO_APPROVE", invoice_date="2026-08-01"):
        rid = uuid.uuid4().hex
        temp_db.create_run(rid, f"{invoice_number}.pdf")
        temp_db.finish_run(rid, decision=decision, vendor_id=vendor_id,
                           invoice_number=invoice_number, po_number=po_number,
                           invoice_total=total, invoice_date=invoice_date)
        return rid
    return _book
