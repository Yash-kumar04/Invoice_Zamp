"""FastAPI application for invoice processing pipeline.

Endpoints per SPEC.md B12:
- POST /api/runs (file upload or sample_id)
- GET  /api/runs/{id}/stream (SSE, replays finished runs or streams live)
- GET  /api/runs
- GET  /api/runs/{id}
- POST /api/runs/{id}/review (approve/reject with note)
- GET  /api/kpis
- GET  /api/config
- GET  /api/master
- GET  /api/samples
- POST /api/reset
- GET  /healthz
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import pymupdf as fitz
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.config import get_as_of_date, load_rules
from app.db import (
    count_runs_by_status,
    create_tables,
    get_all_purchase_orders,
    get_all_vendors,
    get_connection,
    get_ledger_entries,
    get_po_lines,
    get_po_remaining_net,
    get_review_actions,
    get_run,
    get_run_checks,
    get_run_events,
    get_runs,
    is_db_seeded,
    post_to_ledger,
    save_review_action,
    update_run_status,
)
from app.extract import extract_with_gemini, get_model_chain
from app.pipeline import run_pipeline
from app.seed import reset_demo_data, seed_all

load_dotenv()
logger = logging.getLogger("invoice.api")

SAMPLES_DIR = Path("samples")
MANIFEST_PATH = Path("samples/manifest.json")

# ── Rate Limiter (20 runs per IP per hour) ───────────────────────────────────

class RateLimiter:
    def __init__(self, max_requests: int = 20, window_seconds: int = 3600):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.requests: dict[str, list[float]] = defaultdict(list)

    def is_allowed(self, ip: str) -> bool:
        now = time.time()
        cutoff = now - self.window_seconds
        self.requests[ip] = [t for t in self.requests[ip] if t > cutoff]
        if len(self.requests[ip]) >= self.max_requests:
            return False
        self.requests[ip].append(now)
        return True


rate_limiter = RateLimiter(max_requests=20, window_seconds=3600)

# Global in-memory SSE listeners: run_id -> list[asyncio.Queue]
RUN_LISTENERS: dict[str, list[asyncio.Queue]] = defaultdict(list)


# ── App Lifespan & Initialization ───────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure database tables exist and master seed data is present on startup
    conn = get_connection()
    try:
        create_tables(conn)
        if not is_db_seeded(conn):
            logger.info("Database empty on startup. Seeding master data and demo history...")
            seed_all(conn)
    finally:
        conn.close()
    yield


app = FastAPI(
    title="Invoice Processing Engine",
    description="Accounts payable invoice decision engine with live SSE streaming.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Ensure static directory exists and mount static assets
Path("static").mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
def serve_index():
    index_file = Path("static/index.html")
    if index_file.exists():
        return FileResponse(index_file)
    return JSONResponse({"message": "Invoice processing API is running. UI under /static."})


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return Response(status_code=204)


# ── Error Handling (No stack traces to client) ───────────────────────────────

@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    logger.error(f"Internal server error: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "error": "Internal server error",
            "detail": "An unexpected error occurred while processing the request.",
        },
    )


# ── File Validation Helpers ──────────────────────────────────────────────────

def validate_upload(file_bytes: bytes, filename: str) -> None:
    # 1. Size limit: 10 MB
    if len(file_bytes) > 10 * 1024 * 1024:
        raise HTTPException(
            status_code=400,
            detail="File size exceeds the 10 MB maximum limit.",
        )

    # 2. Magic bytes check
    is_pdf = file_bytes.startswith(b"%PDF")
    is_png = file_bytes.startswith(b"\x89PNG\r\n\x1a\n")
    is_jpg = file_bytes.startswith(b"\xff\xd8\xff")
    if not (is_pdf or is_png or is_jpg):
        raise HTTPException(
            status_code=400,
            detail="Invalid file format. Only PDF, PNG, and JPEG documents are permitted.",
        )

    # 3. Page count limit: 5 pages maximum
    try:
        doc = fitz.open(stream=file_bytes, filetype="pdf" if is_pdf else None)
        page_count = len(doc)
        if page_count > 5:
            raise HTTPException(
                status_code=400,
                detail=f"Document exceeds 5-page limit (contains {page_count} pages).",
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"Could not inspect document pages: {e}")


# ── Endpoints ────────────────────────────────────────────────────────────────

@app.get("/healthz")
def healthz():
    """Health check endpoint."""
    return {"status": "ok", "timestamp": datetime.now().isoformat()}


@app.get("/api/config")
def get_config():
    """Return configuration thresholds, AS_OF_DATE and available Gemini models."""
    rules = load_rules()
    as_of = get_as_of_date()
    models = get_model_chain()
    return {
        "rules": rules,
        "as_of_date": as_of.isoformat(),
        "models": models,
    }


@app.get("/api/master")
def get_master():
    """Return master vendors with aliases and purchase orders with lines and remaining balances."""
    conn = get_connection()
    try:
        vendors = [dict(v) for v in get_all_vendors(conn)]
        for v in vendors:
            v["aliases"] = [
                r["alias"]
                for r in conn.execute(
                    "SELECT alias FROM vendor_aliases WHERE vendor_id=?", (v["id"],)
                ).fetchall()
            ]

        pos = [dict(p) for p in get_all_purchase_orders(conn)]
        for p in pos:
            p["remaining_net"] = get_po_remaining_net(conn, p["po_number"])
            lines = [dict(l) for l in get_po_lines(conn, p["po_number"])]
            p["lines"] = lines

        return {
            "vendors": vendors,
            "purchase_orders": pos,
        }
    finally:
        conn.close()


@app.get("/api/samples")
def get_samples():
    """Return list of sample invoices with metadata in demo order."""
    from scenarios.scenarios import SCENARIOS

    demo_order = ["S0", "S1", "E1a", "E1b", "E2", "E4", "E3"]
    labels = {
        "S0": "Happy path",
        "S1": "Within tolerance",
        "E1a": "Split bill 1",
        "E1b": "Split bill 2",
        "E2": "Duplicate scan",
        "E4": "Bank change",
        "E3": "Missing info scan",
    }
    samples = []
    for s_id in demo_order:
        s = SCENARIOS.get(s_id)
        if not s:
            continue
        pdf_path = SAMPLES_DIR / s["file_name"]
        file_size = pdf_path.stat().st_size if pdf_path.exists() else 0
        samples.append({
            "id": s["id"],
            "name": f"{s['id']}: {s['vendor_name']} ({s['file_name']})",
            "label": labels.get(s["id"], ""),
            "vendor_id": s["vendor_id"],
            "vendor_name": s["vendor_name"],
            "invoice_number": s["invoice_number"],
            "po_reference": s["po_reference"],
            "total_amount": s["total_amount"],
            "file_name": s["file_name"],
            "file_size": file_size,
            "is_image_only": s.get("is_image_only", False),
            "layout": s.get("layout", "classic"),
        })
    return {"samples": samples}


@app.get("/api/samples/{sample_id}/file")
def get_sample_file(sample_id: str):
    """Serve the sample document file for browser preview."""
    from scenarios.scenarios import SCENARIOS
    s = SCENARIOS.get(sample_id)
    if not s:
        raise HTTPException(status_code=404, detail="Sample scenario not found.")
    pdf_path = SAMPLES_DIR / s["file_name"]
    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail=f"Sample file {s['file_name']} not found.")
    media_type = "image/png" if s.get("is_image_only") and s["file_name"].endswith(".png") else "application/pdf"
    return FileResponse(pdf_path, media_type=media_type)


@app.get("/api/runs/{run_id}/file")
def get_run_file(run_id: str):
    """Serve document file for an existing run."""
    conn = get_connection()
    try:
        r = get_run(conn, run_id)
        if not r:
            raise HTTPException(status_code=404, detail=f"Run {run_id} not found.")
        r_dict = dict(r)
        fname = r_dict.get("file_name")
        fhash = r_dict.get("file_hash")

        # 1. Check samples
        if fname:
            sample_path = SAMPLES_DIR / fname
            if sample_path.exists():
                return FileResponse(sample_path, media_type="application/pdf")

        # 2. Check uploads directory
        uploads_dir = Path("uploads")
        if fhash:
            for ext in (".pdf", ".png", ".jpg", ".jpeg"):
                candidate = uploads_dir / f"{fhash}{ext}"
                if candidate.exists():
                    media_type = "application/pdf" if ext == ".pdf" else "image/png"
                    return FileResponse(candidate, media_type=media_type)

        raise HTTPException(status_code=404, detail="Original document file not found.")
    finally:
        conn.close()


@app.post("/api/reset")
def reset_data():
    """Reset all database tables to initial demo state."""
    conn = get_connection()
    try:
        reset_demo_data(conn)
        return {"status": "ok", "message": "Demo data reset successfully."}
    finally:
        conn.close()


@app.post("/api/runs")
async def create_run(
    request: Request,
    file: Optional[UploadFile] = File(None),
    sample_id: Optional[str] = Form(None),
):
    """Create a new invoice run from file upload or sample chip. Returns run_id."""
    client_ip = request.client.host if request.client else "127.0.0.1"
    if not rate_limiter.is_allowed(client_ip):
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded: maximum 20 runs per IP per hour.",
        )

    file_bytes: bytes = b""
    filename: str = ""

    # Check JSON body if multipart was not used
    if not file and not sample_id:
        try:
            body = await request.json()
            sample_id = body.get("sample_id")
        except Exception:
            pass

    if sample_id:
        from scenarios.scenarios import SCENARIOS
        scenario = SCENARIOS.get(sample_id)
        if not scenario:
            raise HTTPException(status_code=400, detail=f"Unknown sample scenario ID: {sample_id}")
        pdf_path = SAMPLES_DIR / scenario["file_name"]
        if not pdf_path.exists():
            raise HTTPException(status_code=404, detail=f"Sample file {scenario['file_name']} not found.")
        file_bytes = pdf_path.read_bytes()
        filename = scenario["file_name"]
    elif file:
        file_bytes = await file.read()
        filename = file.filename or "uploaded_invoice.pdf"
    else:
        raise HTTPException(status_code=400, detail="Must provide either a file upload or sample_id.")

    # Validate file type, size, and page count
    validate_upload(file_bytes, filename)

    # Save file to uploads cache for preview
    uploads_dir = Path("uploads")
    uploads_dir.mkdir(exist_ok=True)
    file_hash = hashlib.sha256(file_bytes).hexdigest()
    ext = Path(filename).suffix or ".pdf"
    (uploads_dir / f"{file_hash}{ext}").write_bytes(file_bytes)

    # Execute pipeline asynchronously in thread
    conn = get_connection()
    loop = asyncio.get_running_loop()

    # Pre-generate run_id for streaming registration
    import uuid
    run_id = f"RUN-{uuid.uuid4().hex[:8].upper()}"

    def event_broadcast(evt: dict[str, Any]):
        for q in RUN_LISTENERS.get(run_id, []):
            try:
                loop.call_soon_threadsafe(q.put_nowait, evt)
            except Exception:
                pass

    def execute_async():
        # Pipeline execution with 350ms display pacing for live viewers
        c = get_connection()
        try:
            run_pipeline(
                file_bytes=file_bytes,
                filename=filename,
                extract_fn=extract_with_gemini,
                db=c,
                on_event=event_broadcast,
                min_display_ms=350.0,
                run_id=run_id,
            )
        finally:
            c.close()

    # Launch background thread
    asyncio.create_task(asyncio.to_thread(execute_async))

    return {"run_id": run_id}


@app.get("/api/runs/{run_id}/stream")
async def stream_run(run_id: str):
    """Server-Sent Events (SSE) endpoint: replays finished runs or streams live events."""
    queue: asyncio.Queue = asyncio.Queue()
    RUN_LISTENERS[run_id].append(queue)

    async def event_generator():
        seen_events = set()
        try:
            # 1. Fetch DB state: if finished, replay; if running, replay prior events
            conn_replay = get_connection()
            try:
                events = get_run_events(conn_replay, run_id)
                current_run = get_run(conn_replay, run_id)
            finally:
                conn_replay.close()

            if current_run and current_run["status"] != "RUNNING" and events:
                for evt in events:
                    yield f"event: {evt['event_type']}\ndata: {json.dumps(evt)}\n\n"
                    # Replay delay capped at 50ms per SPEC D6
                    await asyncio.sleep(min(0.05, float(evt.get("duration_ms") or 0.0) / 1000.0))

                # Yield final decision record
                try:
                    dec = get_run_detail(run_id)
                except Exception:
                    dec = dict(current_run)
                yield f"event: done\ndata: {json.dumps(dec)}\n\n"
                return

            # If still running, replay already emitted events first
            for evt in (events or []):
                key = (evt.get("stage_number"), evt.get("event_type"))
                seen_events.add(key)
                yield f"event: {evt['event_type']}\ndata: {json.dumps(evt)}\n\n"

            # 2. Live streaming from queue
            while True:
                try:
                    evt = await asyncio.wait_for(queue.get(), timeout=60.0)
                    key = (evt.get("stage_number"), evt.get("event_type"))
                    if key in seen_events:
                        continue
                    seen_events.add(key)
                    yield f"event: {evt['event_type']}\ndata: {json.dumps(evt)}\n\n"

                    # If Stage 12 finished or early failure, emit final done event
                    is_stage12_finished = (evt.get("stage_number") == 12 and evt.get("event_type") == "finished")
                    is_stage2_failed = (evt.get("stage_number") == 2 and evt.get("event_type") == "finished" and evt.get("data", {}).get("result") == "fail")
                    if is_stage12_finished or is_stage2_failed:
                        try:
                            dec = get_run_detail(run_id)
                        except Exception as ex:
                            logger.error(f"Failed to get run detail for {run_id}: {ex}")
                            dec = {"run_id": run_id, "status": "ERROR"}
                        yield f"event: done\ndata: {json.dumps(dec)}\n\n"
                        break
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            if queue in RUN_LISTENERS.get(run_id, []):
                RUN_LISTENERS[run_id].remove(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/runs")
def list_runs(
    status: Optional[str] = Query(None),
    vendor_id: Optional[str] = Query(None),
    seeded: Optional[bool] = Query(None),
):
    """Return past runs sorted newest first, with optional filters."""
    conn = get_connection()
    try:
        sql = "SELECT * FROM runs WHERE 1=1"
        params = []
        if status:
            sql += " AND status = ?"
            params.append(status)
        if vendor_id:
            sql += " AND vendor_id = ?"
            params.append(vendor_id)
        if seeded is not None:
            sql += " AND seeded = ?"
            params.append(1 if seeded else 0)
        sql += " ORDER BY created_at DESC"
        rows = conn.execute(sql, params).fetchall()

        runs = []
        for r in rows:
            d = dict(r)
            if d.get("decision_json"):
                try:
                    d["decision"] = json.loads(d["decision_json"])
                except Exception:
                    pass
            runs.append(d)
        return {"runs": runs}
    finally:
        conn.close()


@app.get("/api/runs/{run_id}")
def get_run_detail(run_id: str):
    """Return complete B10 decision record for a specific run."""
    conn = get_connection()
    try:
        r = get_run(conn, run_id)
        if not r:
            raise HTTPException(status_code=404, detail=f"Run {run_id} not found.")

        r_dict = dict(r)
        # Return full decision_json if present
        if r_dict.get("decision_json"):
            try:
                record = json.loads(r_dict["decision_json"])
                return record
            except Exception:
                pass

        # Fallback assemble
        checks = [dict(c) for c in get_run_checks(conn, run_id)]
        for c in checks:
            if c.get("evidence") and isinstance(c["evidence"], str):
                try:
                    c["evidence"] = json.loads(c["evidence"])
                except Exception:
                    pass
        res = r_dict
        res["checks"] = checks
        if res.get("extraction_json"):
            try:
                res["extraction"] = {
                    "fields": json.loads(res["extraction_json"]),
                    "source": "live",
                    "model": "gemini",
                }
            except Exception:
                pass
        res["vendor"] = {"id": res.get("vendor_id"), "name": res.get("vendor_name")}
        res["po"] = {"po_number": res.get("po_number")}
        return res
    finally:
        conn.close()


class ReviewRequest(BaseModel):
    action: str  # 'approve' or 'reject'
    note: Optional[str] = None
    reviewer: Optional[str] = "demo_user"


@app.post("/api/runs/{run_id}/review")
def review_run(run_id: str, req: ReviewRequest):
    """Human review action: approve posts to ledger; reject sets reason; both write an audit row."""
    if req.action.lower() not in ("approve", "reject"):
        raise HTTPException(status_code=400, detail="Action must be 'approve' or 'reject'.")

    conn = get_connection()
    try:
        r = get_run(conn, run_id)
        if not r:
            raise HTTPException(status_code=404, detail=f"Run {run_id} not found.")

        r_dict = dict(r)
        new_status = "APPROVED" if req.action.lower() == "approve" else "REJECTED"
        update_run_status(conn, run_id, new_status)

        # Audit review action
        save_review_action(
            conn,
            run_id=run_id,
            action=req.action.lower(),
            note=req.note,
            reviewer=req.reviewer or "demo_user",
        )

        # If approved, post to ledger if not already posted
        ledger_posted = False
        ledger_id = None
        if new_status == "APPROVED" and r_dict.get("vendor_id") and r_dict.get("po_number"):
            # Check if already in ledger
            existing = conn.execute("SELECT id FROM ledger WHERE run_id = ?", (run_id,)).fetchone()
            if not existing:
                as_of = get_as_of_date()
                ledger_id = post_to_ledger(
                    conn,
                    invoice_number=r_dict.get("invoice_number") or r_dict.get("file_name") or run_id,
                    vendor_id=r_dict["vendor_id"],
                    po_number=r_dict["po_number"],
                    net=float(r_dict.get("net") or 0.0),
                    gross=float(r_dict.get("gross") or 0.0),
                    invoice_date=str(r_dict.get("invoice_date") or as_of.isoformat()),
                    posted_at=as_of.isoformat(),
                    run_id=run_id,
                )
                ledger_posted = True

        # Update decision_json if present
        if r_dict.get("decision_json"):
            try:
                dec = json.loads(r_dict["decision_json"])
                dec["status"] = new_status
                if ledger_posted:
                    dec["ledger_effect"] = {"posted": True, "ledger_id": ledger_id}
                conn.execute(
                    "UPDATE runs SET decision_json = ? WHERE run_id = ?",
                    (json.dumps(dec), run_id),
                )
                conn.commit()
                return dec
            except Exception:
                pass

        return {
            "run_id": run_id,
            "status": new_status,
            "action": req.action.lower(),
            "note": req.note,
            "ledger_posted": ledger_posted,
        }
    finally:
        conn.close()


@app.get("/api/kpis")
def get_kpis():
    """Compute dashboard KPIs per SPEC B12."""
    conn = get_connection()
    try:
        all_runs = conn.execute("SELECT * FROM runs").fetchall()
        total_processed = len(all_runs)

        approved_runs = [r for r in all_runs if r["status"] == "APPROVED"]
        approved_count = len(approved_runs)

        reviewed_run_ids = {
            r["run_id"] for r in conn.execute("SELECT DISTINCT run_id FROM review_actions").fetchall()
        }
        touchless_count = sum(1 for r in approved_runs if r["run_id"] not in reviewed_run_ids)
        touchless_rate = round((touchless_count / total_processed * 100), 1) if total_processed > 0 else 0.0

        times = [r["processing_time_ms"] for r in all_runs if r["processing_time_ms"] is not None]
        avg_proc_ms = round(sum(times) / len(times), 1) if times else 0.0

        config = load_rules()
        manual_min = float(config.get("manual_minutes_per_invoice", 8.0))
        hours_saved = round((manual_min * touchless_count) / 60.0, 1)

        at_risk_runs = [
            r for r in all_runs
            if r["status"] == "REJECTED" or r["severity"] == "CRITICAL"
        ]
        amount_at_risk = round(sum(float(r["gross"] or 0.0) for r in at_risk_runs), 2)

        top_rules_rows = conn.execute(
            """SELECT check_id, check_name, COUNT(*) as cnt
               FROM run_checks
               WHERE result IN ('fail', 'warn')
               GROUP BY check_id
               ORDER BY cnt DESC
               LIMIT 3"""
        ).fetchall()
        top_rules = [
            {"id": r["check_id"], "name": r["check_name"], "count": r["cnt"]}
            for r in top_rules_rows
        ]

        return {
            "invoices_processed": total_processed,
            "touchless_rate_pct": touchless_rate,
            "touchless_count": touchless_count,
            "avg_processing_time_ms": avg_proc_ms,
            "manual_benchmark_minutes": manual_min,
            "hours_saved": hours_saved,
            "amount_at_risk_caught": amount_at_risk,
            "top_rules_fired": top_rules,
        }
    finally:
        conn.close()
