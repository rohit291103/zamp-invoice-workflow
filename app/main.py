"""HTTP surface: a live run view over server-sent events, plus the dashboard.

The web layer deliberately contains no process logic. It streams whatever
run_pipeline() emits and renders it. Anything you can see in the UI you can also
get from scripts/run_cli.py, because both drive the identical generator.
"""
from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path
from typing import Any, AsyncIterator, Optional

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import db
from .catalogue import CATALOGUE
from .config import INVOICE_DIR
from .extractors.ingest import render_page_png
from .masterdata import load_pos, load_vendors
from .pipeline import run_pipeline

STATIC = Path(__file__).parent / "static"
UPLOADS = INVOICE_DIR / "uploads"

# Rasterising a page costs ~100ms; the same few pages get requested constantly.
_PREVIEW_CACHE: dict[tuple, bytes] = {}

@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init()
    # If the process was killed mid-run last time, no cleanup code ran at all.
    # Settle those rows now so they cannot skew the dashboard forever.
    swept = db.sweep_stale_runs()
    if swept:
        print(f"settled {swept} interrupted run(s) from a previous session")
    UPLOADS.mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(title="AP Invoice Process", docs_url="/api/docs", lifespan=lifespan)


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (STATIC / "index.html").read_text(encoding="utf-8")


@app.get("/api/invoices")
def api_invoices() -> list[dict[str, Any]]:
    out = []
    for name, meta in CATALOGUE.items():
        p = INVOICE_DIR / name
        if p.exists():
            out.append({"file": name, "size": p.stat().st_size, **meta})
    for p in sorted(UPLOADS.glob("*.pdf")):
        out.append({"file": f"uploads/{p.name}", "title": p.name, "kind": "upload",
                    "blurb": "Uploaded by you.", "expect": None,
                    "size": p.stat().st_size})
    return out


@app.get("/api/invoices/file")
def api_invoice_file(name: str) -> FileResponse:
    p = (INVOICE_DIR / name).resolve()
    if not p.is_relative_to(INVOICE_DIR.resolve()) or not p.exists():
        raise HTTPException(404, "not found")
    return FileResponse(p, media_type="application/pdf")


@app.get("/api/invoices/preview")
def api_invoice_preview(name: str, scale: float = 1.6) -> Response:
    """Page 1 rendered to a PNG.

    Deliberately not an <iframe> of the PDF: browsers disagree about whether
    they will render a PDF inline at all, and headless ones simply don't. An
    image always shows. It is also honest about what the vision path actually
    receives for a scanned invoice - the same rasterised page.
    """
    p = (INVOICE_DIR / name).resolve()
    if not p.is_relative_to(INVOICE_DIR.resolve()) or not p.exists():
        raise HTTPException(404, "not found")
    key = (p, p.stat().st_mtime, round(scale, 2))
    png = _PREVIEW_CACHE.get(key)
    if png is None:
        png = render_page_png(p, 0, max(0.5, min(scale, 3.0)))
        if len(_PREVIEW_CACHE) > 32:
            _PREVIEW_CACHE.clear()
        _PREVIEW_CACHE[key] = png
    return Response(png, media_type="image/png",
                    headers={"Cache-Control": "public, max-age=3600"})


@app.post("/api/upload")
async def api_upload(file: UploadFile = File(...)) -> dict[str, str]:
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "PDF files only")
    UPLOADS.mkdir(parents=True, exist_ok=True)
    safe = Path(file.filename).name
    dest = UPLOADS / f"{uuid.uuid4().hex[:6]}_{safe}"
    with dest.open("wb") as fh:
        shutil.copyfileobj(file.file, fh)
    return {"file": f"uploads/{dest.name}"}


@app.get("/api/run/stream")
async def api_run_stream(file: str) -> StreamingResponse:
    """Execute the pipeline and stream each stage as it completes."""
    path = (INVOICE_DIR / file).resolve()
    if not path.is_relative_to(INVOICE_DIR.resolve()) or not path.exists():
        raise HTTPException(404, f"no such invoice: {file}")
    scenario = CATALOGUE.get(file, {}).get("title")

    async def events() -> AsyncIterator[bytes]:
        # Hold the generator so we can close it deterministically. Without the
        # explicit aclose(), a browser that navigates away mid-run leaves the
        # pipeline suspended and its run row stuck at 'running'.
        gen = run_pipeline(path, scenario=scenario)
        try:
            async for ev in gen:
                yield f"data: {json.dumps(ev, default=str)}\n\n".encode()
            yield b"data: {\"type\": \"stream_end\"}\n\n"
        except Exception as exc:  # never leave the client hanging
            yield f"data: {json.dumps({'type': 'run_failed', 'error': str(exc)})}\n\n".encode()
        finally:
            await gen.aclose()

    return StreamingResponse(events(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"})


@app.get("/api/runs")
def api_runs(limit: int = 200) -> list[dict[str, Any]]:
    return db.list_runs(limit)


@app.get("/api/runs/{run_id}")
def api_run(run_id: str) -> dict[str, Any]:
    run = db.get_run(run_id)
    if not run:
        raise HTTPException(404, "no such run")
    return run


@app.post("/api/runs/{run_id}/action")
def api_action(run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """The human-in-the-loop step. Approving a held invoice changes what later
    invoices see - an approved amount starts consuming its PO."""
    action = payload.get("action")
    if action not in ("approved", "rejected"):
        raise HTTPException(400, "action must be 'approved' or 'rejected'")
    if not db.get_run(run_id):
        raise HTTPException(404, "no such run")
    db.set_human_action(run_id, action, payload.get("note", ""))
    return {"ok": True, "run_id": run_id, "action": action}


@app.get("/api/stats")
def api_stats() -> dict[str, Any]:
    return db.stats()


@app.get("/api/reference")
def api_reference() -> dict[str, Any]:
    """Vendor and PO master data, with live consumption per PO - this is what
    makes the split-billing edge case legible during a demo."""
    from .matching import tolerance_for
    pos = []
    for po in load_pos():
        ledger = db.po_ledger(po.po_number, "")
        tol = tolerance_for(po)
        # "Over" means over the PO *plus its tolerance* - a variance the process
        # would have accepted is not an overrun.
        over = max(0.0, ledger["consumed"] - po.po_amount - tol)
        pos.append({
            "po_number": po.po_number, "vendor_id": po.vendor_id,
            "description": po.description, "currency": po.currency,
            "po_amount": po.po_amount, "status": po.status,
            "allow_partial": po.allow_partial, "tolerance_pct": po.tolerance_pct,
            "billed": ledger["billed"], "encumbered": ledger["encumbered"],
            "consumed": ledger["consumed"],
            "remaining": round(po.po_amount - ledger["consumed"], 2),
            "tolerance": round(tol, 2),
            "over": round(over, 2),
            "entries": ledger["entries"],
        })
    return {
        "vendors": [v.__dict__ for v in load_vendors()],
        "purchase_orders": pos,
    }


@app.post("/api/reset")
def api_reset() -> dict[str, Any]:
    """Clear run history so a demo can be re-run from a clean slate."""
    from .config import DB_PATH
    DB_PATH.unlink(missing_ok=True)
    db.init()
    return {"ok": True}


app.mount("/static", StaticFiles(directory=STATIC), name="static")
