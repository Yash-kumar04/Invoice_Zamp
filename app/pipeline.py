"""12-Stage invoice processing pipeline engine.

Executes stages 1 to 12 in exact order per SPEC.md B5.
Extract function is injected: uses fixtures/manifest during testing; Gemini in Phase 2.
Persists runs, checks, and stage events to DB. Posts to ledger ONLY when APPROVED.
Zero branching on scenario id or file name.
"""

from datetime import date, datetime
import hashlib
import json
import logging
import time
from typing import Any, Callable
import uuid

import pymupdf as fitz

logger = logging.getLogger("invoice.pipeline")

from app.config import get_as_of_date, load_rules
from app.db import (
    get_all_vendors,
    get_all_purchase_orders,
    get_po_lines,
    get_po_remaining_net,
    get_po_line_remaining_qty,
    get_ledger_entries,
    get_runs,
    save_run,
    post_to_ledger,
)
from app.rules import (
    Check,
    invoice_number_key,
    po_key,
    vendor_name_normalise,
    calculate_invoice_net,
    check_ext_01,
    check_ext_02,
    check_ext_03,
    check_ext_04,
    check_ven_01,
    check_ven_02,
    check_ven_03,
    check_ven_04,
    check_ven_05,
    check_dup_01,
    check_dup_02,
    check_po_01,
    check_po_02,
    check_po_03,
    check_line_01,
    check_3w_01,
    check_bal_01,
    check_tax_01,
    check_amt_01,
    check_pol_01,
    decide,
)


STAGE_NAMES = {
    1: "Ingest",
    2: "Extract",
    3: "Verify extraction",
    4: "Normalise",
    5: "Vendor check",
    6: "Duplicate check",
    7: "PO match",
    8: "Lines and 3-way",
    9: "Amounts",
    10: "Policy",
    11: "Decide and explain",
    12: "Act",
}


def _compute_stage_result(checks: list[Check]) -> str:
    """Aggregate check results into stage result: fail > warn > pass > skip."""
    if not checks:
        return "pass"
    results = {c.result for c in checks}
    if "fail" in results:
        return "fail"
    if "warn" in results:
        return "warn"
    if "pass" in results:
        return "pass"
    return "skip"


def run_pipeline(
    file_bytes: bytes,
    filename: str,
    extract_fn: Callable[[bytes, str, str], dict] | None = None,
    db: Any = None,
    on_event: Callable[[dict], None] | None = None,
    min_display_ms: float = 0.0,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Execute the 12-stage invoice processing pipeline."""
    if run_id is None:
        run_id = f"RUN-{uuid.uuid4().hex[:8].upper()}"
    start_total_time = time.perf_counter()
    created_at = datetime.now().isoformat()
    as_of = get_as_of_date()
    config = load_rules()

    all_checks: list[Check] = []
    stage_events: list[dict[str, Any]] = []
    timings: dict[str, float] = {}

    def emit(stage_no: int, event_type: str, data: dict[str, Any] | None = None, duration_ms: float = 0.0) -> None:
        evt = {
            "run_id": run_id,
            "stage_number": stage_no,
            "stage_name": STAGE_NAMES[stage_no],
            "event_type": event_type,
            "data": data or {},
            "duration_ms": round(duration_ms, 2),
            "created_at": datetime.now().isoformat(),
        }
        stage_events.append(evt)

        # Persist event to DB run_events table
        if db is not None:
            try:
                db.execute(
                    """INSERT INTO run_events
                       (run_id, stage_number, stage_name, event_type, data_json, duration_ms, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, datetime('now'))""",
                    (
                        run_id,
                        stage_no,
                        STAGE_NAMES[stage_no],
                        event_type,
                        json.dumps(data or {}),
                        round(duration_ms, 2),
                    ),
                )
                db.commit()
            except Exception as e:
                logger.warning(f"Failed to persist run_event: {e}")

        if on_event:
            try:
                on_event(evt)
            except Exception:
                pass

        if min_display_ms > 0 and event_type == "finished":
            elapsed_sec = duration_ms / 1000.0
            target_sec = min_display_ms / 1000.0
            if elapsed_sec < target_sec:
                time.sleep(target_sec - elapsed_sec)

    file_hash = hashlib.sha256(file_bytes).hexdigest()

    # Pre-insert run record so run_events foreign key constraint is satisfied
    if db is not None:
        try:
            db.execute(
                """INSERT OR REPLACE INTO runs
                   (run_id, file_name, file_hash, status, severity, created_at, seeded)
                   VALUES (?, ?, ?, 'RUNNING', 'LOW', ?, 0)""",
                (run_id, filename, file_hash, created_at),
            )
            db.commit()
        except Exception as e:
            logger.warning(f"Failed to insert initial run: {e}")

    # ── Stage 1: Ingest ─────────────────────────────────────────────────
    t0 = time.perf_counter()
    emit(1, "started", {"stage": STAGE_NAMES[1]})

    text_layer = ""
    is_scanned = True
    page_count = 1

    try:
        doc = fitz.open(stream=file_bytes, filetype="pdf")
        page_count = len(doc)
        text_layer = "".join(p.get_text() for p in doc)
        is_scanned = len(text_layer.strip()) == 0
    except Exception:
        # Non-PDF or raster image
        is_scanned = True

    t1_ms = (time.perf_counter() - t0) * 1000
    timings["ingest"] = t1_ms
    doc_type = "scanned PDF" if is_scanned else "digital PDF"
    page_str = f"{page_count} page" if page_count == 1 else f"{page_count} pages"
    stage1_summary = f"{page_str}, {doc_type}, {len(file_bytes)/1024:.1f} KB, SHA-256 {file_hash[:8]}."
    emit(1, "finished", {
        "result": "pass",
        "duration_ms": round(t1_ms, 2),
        "summary": stage1_summary,
        "findings": {"file_hash": file_hash, "is_scanned": is_scanned, "page_count": page_count},
    }, duration_ms=t1_ms)

    # ── Stage 2: Extract ────────────────────────────────────────────────
    t0 = time.perf_counter()
    emit(2, "started", {"stage": STAGE_NAMES[2]})

    if extract_fn is None:
        from app.extract import extract_with_gemini
        extract_fn = extract_with_gemini

    try:
        ext_res = extract_fn(file_bytes, filename, file_hash)
        extraction = ext_res.get("fields", ext_res)
        ext_source = ext_res.get("_source", ext_res.get("source", "live"))
        ext_model = ext_res.get("_model", ext_res.get("model", "gemini"))
    except Exception as ex:
        t2_ms = (time.perf_counter() - t0) * 1000
        timings["extract"] = t2_ms
        stage2_summary = f"Extraction failed: {ex}"
        emit(2, "finished", {
            "result": "fail",
            "duration_ms": round(t2_ms, 2),
            "summary": stage2_summary,
            "findings": {"error": str(ex)},
        }, duration_ms=t2_ms)

        # Build ERROR decision record
        err_rec = {
            "run_id": run_id,
            "status": "ERROR",
            "severity": "HIGH",
            "headline": f"Extraction failed: {ex}",
            "reasons": [str(ex)],
            "next_action": "retry_run",
            "vendor": {"id": None, "name": None, "tax_id": None, "status": "unknown", "callback_phone": None},
            "po": {"po_number": None, "remaining_net": None, "tax_rate": None},
            "gross": None,
            "net": None,
            "currency": "USD",
            "pay_by": None,
            "checks": [],
            "extraction": {"fields": {}, "source": "failed", "model": "none", "verified": False},
            "timings": timings,
            "email_draft": None,
            "ledger_effect": {"posted": False, "reason": "Extraction failed."},
            "stage_events": stage_events,
        }
        if db is not None:
            save_run(db, {
                "run_id": run_id,
                "invoice_number": None,
                "vendor_id": None,
                "vendor_name": None,
                "gross": 0.0,
                "status": "ERROR",
                "severity": "HIGH",
                "headline": err_rec["headline"],
                "reasons": err_rec["reasons"],
                "next_action": "retry_run",
                "file_hash": file_hash,
                "created_at": created_at,
                "decision_json": json.dumps(err_rec),
            }, checks=[])
        return err_rec

    t2_ms = (time.perf_counter() - t0) * 1000
    timings["extract"] = t2_ms
    v_name = extraction.get("vendor_name") or "Unknown vendor"
    stage2_summary = f"Extracted fields for {v_name} (source: {ext_source}, model: {ext_model})."
    emit(2, "finished", {
        "result": "pass",
        "duration_ms": round(t2_ms, 2),
        "summary": stage2_summary,
        "findings": {
            **extraction,
            "source": ext_source,
            "model": ext_model,
        },
    }, duration_ms=t2_ms)

    # ── Stage 3: Verify Extraction ──────────────────────────────────────
    t0 = time.perf_counter()
    emit(3, "started", {"stage": STAGE_NAMES[3]})

    c_ext01 = check_ext_01(
        vendor_name=extraction.get("vendor_name"),
        invoice_number=extraction.get("invoice_number"),
        invoice_date=extraction.get("invoice_date"),
        total_amount=extraction.get("total_amount"),
        currency=extraction.get("currency"),
    )
    c_ext02 = check_ext_02(
        text_layer=text_layer,
        invoice_number=extraction.get("invoice_number"),
        total_amount=extraction.get("total_amount"),
        is_scanned=is_scanned,
        document_quality=extraction.get("document_quality"),
    )
    c_ext03 = check_ext_03(
        line_items=extraction.get("line_items"),
        subtotal=extraction.get("subtotal"),
        tax_amount=extraction.get("tax_amount"),
        discount=extraction.get("discount"),
        total_amount=extraction.get("total_amount"),
        tolerance=config.get("arithmetic_tolerance", 0.02),
        tax_mode=extraction.get("tax_mode"),
    )
    c_ext04 = check_ext_04(
        invoice_date=extraction.get("invoice_date"),
        due_date=extraction.get("due_date"),
        as_of_date=as_of,
        stale_days=config.get("stale_invoice_days", 180),
    )

    s3_checks = [c_ext01, c_ext02, c_ext03, c_ext04]
    all_checks.extend(s3_checks)
    s3_result = _compute_stage_result(s3_checks)
    t3_ms = (time.perf_counter() - t0) * 1000
    timings["verify"] = t3_ms
    if s3_result in ("fail", "warn"):
        failing_c = next((c for c in s3_checks if c.result in ("fail", "warn")), None)
        stage3_summary = failing_c.message if failing_c else "Verification check flagged issues."
    else:
        stage3_summary = "Verified required fields, text grounding, arithmetic and dates."
    emit(3, "finished", {
        "result": s3_result,
        "duration_ms": round(t3_ms, 2),
        "summary": stage3_summary,
        "findings": [c.to_dict() for c in s3_checks],
    }, duration_ms=t3_ms)

    # ── Stage 4: Normalise ──────────────────────────────────────────────
    t0 = time.perf_counter()
    emit(4, "started", {"stage": STAGE_NAMES[4]})

    inv_key = invoice_number_key(extraction.get("invoice_number"))
    po_k = po_key(extraction.get("po_reference"))
    v_norm = vendor_name_normalise(extraction.get("vendor_name"))
    inv_net, tax_inferred = calculate_invoice_net(
        subtotal=extraction.get("subtotal"),
        total=extraction.get("total_amount"),
        tax_amount=extraction.get("tax_amount"),
        tax_mode=extraction.get("tax_mode"),
    )

    t4_ms = (time.perf_counter() - t0) * 1000
    timings["normalise"] = t4_ms
    stage4_summary = f"Normalised invoice key ({inv_key or 'none'}), PO key ({po_k or 'none'}), and calculated net (${inv_net or 0:,.2f})."
    emit(4, "finished", {
        "result": "pass",
        "duration_ms": round(t4_ms, 2),
        "summary": stage4_summary,
        "findings": {
            "invoice_key": inv_key,
            "po_key": po_k,
            "vendor_normalised": v_norm,
            "net": inv_net,
            "tax_inferred": tax_inferred,
        },
    }, duration_ms=t4_ms)

    # ── Stage 5: Vendor Check ───────────────────────────────────────────
    t0 = time.perf_counter()
    emit(5, "started", {"stage": STAGE_NAMES[5]})

    master_vendors = [dict(v) for v in get_all_vendors(db)]
    for mv in master_vendors:
        mv["aliases"] = [r["alias"] for r in db.execute("SELECT alias FROM vendor_aliases WHERE vendor_id=?", (mv["id"],)).fetchall()]

    c_ven01, matched_vendor = check_ven_01(
        printed_name=extraction.get("vendor_name"),
        master_vendors=master_vendors,
        accept_score=config.get("vendor_fuzzy_accept", 92.0),
        review_score=config.get("vendor_fuzzy_review", 80.0),
    )
    c_ven02 = check_ven_02(matched_vendor)
    c_ven03 = check_ven_03(extraction.get("vendor_tax_id"), matched_vendor)
    c_ven04 = check_ven_04(extraction.get("bank_account_last4"), matched_vendor)
    c_ven05 = check_ven_05(extraction.get("vendor_email"), matched_vendor)

    s5_checks = [c_ven01, c_ven02, c_ven03, c_ven04, c_ven05]
    all_checks.extend(s5_checks)
    s5_result = _compute_stage_result(s5_checks)
    t5_ms = (time.perf_counter() - t0) * 1000
    timings["vendor"] = t5_ms
    v_display = matched_vendor["name"] if matched_vendor else "Unknown"
    if s5_result in ("fail", "warn"):
        failing_c = next((c for c in s5_checks if c.result in ("fail", "warn")), None)
        stage5_summary = failing_c.message if failing_c else f"Vendor check flagged issues for {v_display}."
    else:
        stage5_summary = f"Vendor resolved to {v_display}. Status, tax ID and banking verified."
    emit(5, "finished", {
        "result": s5_result,
        "duration_ms": round(t5_ms, 2),
        "summary": stage5_summary,
        "findings": [c.to_dict() for c in s5_checks],
    }, duration_ms=t5_ms)

    # ── Stage 6: Duplicate Check ────────────────────────────────────────
    t0 = time.perf_counter()
    emit(6, "started", {"stage": STAGE_NAMES[6]})

    ledger_entries = [dict(r) for r in get_ledger_entries(db)]
    past_runs = [dict(r) for r in get_runs(db) if r["run_id"] != run_id]

    v_id = matched_vendor["id"] if matched_vendor else None
    c_dup01 = check_dup_01(
        vendor_id=v_id,
        invoice_number=extraction.get("invoice_number"),
        file_hash=file_hash,
        ledger_entries=ledger_entries,
        past_runs=past_runs,
    )
    c_dup02 = check_dup_02(
        vendor_id=v_id,
        invoice_number=extraction.get("invoice_number"),
        invoice_date=extraction.get("invoice_date"),
        total_amount=extraction.get("total_amount"),
        ledger_entries=ledger_entries,
        dup_similarity=config.get("dup_number_similarity", 85.0),
        dup_window_days=config.get("dup_date_window_days", 10),
    )

    s6_checks = [c_dup01, c_dup02]
    all_checks.extend(s6_checks)
    s6_result = _compute_stage_result(s6_checks)
    t6_ms = (time.perf_counter() - t0) * 1000
    timings["duplicate"] = t6_ms
    if s6_result in ("fail", "warn"):
        failing_c = next((c for c in s6_checks if c.result in ("fail", "warn")), None)
        stage6_summary = failing_c.message if failing_c else "Duplicate check flagged potential match."
    else:
        stage6_summary = "Checked exact and probable duplicates against posted ledger."
    emit(6, "finished", {
        "result": s6_result,
        "duration_ms": round(t6_ms, 2),
        "summary": stage6_summary,
        "findings": [c.to_dict() for c in s6_checks],
    }, duration_ms=t6_ms)

    # ── Stage 7: PO Match ───────────────────────────────────────────────
    t0 = time.perf_counter()
    emit(7, "started", {"stage": STAGE_NAMES[7]})

    open_pos = [dict(p) for p in get_all_purchase_orders(db)]
    c_po01, matched_po, suggested_po = check_po_01(
        po_reference=extraction.get("po_reference"),
        vendor_id=v_id,
        open_pos=open_pos,
        total_amount=extraction.get("total_amount"),
        line_items=extraction.get("line_items"),
        implied_min_score=config.get("implied_po_min_score", 85.0),
    )

    c_po02 = check_po_02(matched_po, v_id)
    rem_po_net = get_po_remaining_net(db, matched_po["po_number"]) if matched_po else None
    c_po03 = check_po_03(matched_po, rem_po_net)

    s7_checks = [c_po01, c_po02, c_po03]
    all_checks.extend(s7_checks)
    s7_result = _compute_stage_result(s7_checks)
    t7_ms = (time.perf_counter() - t0) * 1000
    timings["po_match"] = t7_ms
    po_display = matched_po["po_number"] if matched_po else (f"Suggested {suggested_po}" if suggested_po else "None")
    if s7_result in ("fail", "warn"):
        failing_c = next((c for c in s7_checks if c.result in ("fail", "warn")), None)
        stage7_summary = failing_c.message if failing_c else f"PO check flagged issues for {po_display}."
    else:
        stage7_summary = f"PO match: {po_display}. Ownership and open balance checked."
    emit(7, "finished", {
        "result": s7_result,
        "duration_ms": round(t7_ms, 2),
        "summary": stage7_summary,
        "findings": [c.to_dict() for c in s7_checks],
    }, duration_ms=t7_ms)

    # ── Stage 8: Lines and 3-Way ────────────────────────────────────────
    t0 = time.perf_counter()
    emit(8, "started", {"stage": STAGE_NAMES[8]})

    po_lines = [dict(l) for l in get_po_lines(db, matched_po["po_number"])] if matched_po else []
    c_line01, matched_pairs = check_line_01(
        invoice_lines=extraction.get("line_items"),
        po_lines=po_lines,
        invoice_net=inv_net,
        po_remaining_net=rem_po_net,
        price_tolerance=config.get("arithmetic_tolerance", 0.02),
    )
    c_3w01 = check_3w_01(
        po=matched_po,
        matched_pairs=matched_pairs,
        get_remaining_qty_fn=lambda p, l: get_po_line_remaining_qty(db, p, l),
    )

    s8_checks = [c_line01, c_3w01]
    all_checks.extend(s8_checks)
    s8_result = _compute_stage_result(s8_checks)
    t8_ms = (time.perf_counter() - t0) * 1000
    timings["lines"] = t8_ms
    if s8_result in ("fail", "warn"):
        failing_c = next((c for c in s8_checks if c.result in ("fail", "warn")), None)
        stage8_summary = failing_c.message if failing_c else "Line matching or 3-way check flagged issues."
    else:
        stage8_summary = "Mapped invoice lines and checked 3-way quantities against receipts."
    emit(8, "finished", {
        "result": s8_result,
        "duration_ms": round(t8_ms, 2),
        "summary": stage8_summary,
        "findings": [c.to_dict() for c in s8_checks],
    }, duration_ms=t8_ms)

    # ── Stage 9: Amounts ────────────────────────────────────────────────
    t0 = time.perf_counter()
    emit(9, "started", {"stage": STAGE_NAMES[9]})

    po_rate = float(matched_po["tax_rate"]) if matched_po else 0.0

    # If tax was inferred or not separated, re-evaluate net using the PO's official tax rate
    if matched_po and (tax_inferred or extraction.get("tax_mode") == "included" or extraction.get("subtotal") is None):
        recalc_net, tax_inferred = calculate_invoice_net(
            subtotal=extraction.get("subtotal"),
            total=extraction.get("total_amount"),
            tax_amount=extraction.get("tax_amount"),
            tax_mode=extraction.get("tax_mode"),
            po_tax_rate=po_rate,
        )
        if recalc_net is not None:
            inv_net = recalc_net

    c_bal01 = check_bal_01(
        invoice_net=inv_net,
        po_remaining_net=rem_po_net,
        tolerance_pct=config.get("tolerance_pct", 2.0),
        tolerance_abs=config.get("tolerance_abs", 100.0),
    )
    c_tax01 = check_tax_01(
        tax_amount=extraction.get("tax_amount"),
        invoice_net=inv_net,
        po_tax_rate=po_rate if matched_po else None,
        tax_inferred=tax_inferred,
        tax_abs_tolerance=config.get("tax_abs_tolerance", 1.00),
    )

    # Determine billed net at PO prices for AMT-01 (SPEC B8)
    if c_line01.evidence.get("bundled_line"):
        billed_net_at_po_prices = rem_po_net
    elif matched_pairs:
        billed_net_at_po_prices = sum(
            float(inv_line.get("quantity", 1)) * float(po_line["unit_price"])
            for inv_line, po_line in matched_pairs
        )
    else:
        billed_net_at_po_prices = inv_net

    c_amt01, variance = check_amt_01(
        total_amount=extraction.get("total_amount"),
        invoice_net=billed_net_at_po_prices,
        po_tax_rate=po_rate if matched_po else None,
        tolerance_pct=config.get("tolerance_pct", 2.0),
        tolerance_abs=config.get("tolerance_abs", 100.0),
    )

    s9_checks = [c_bal01, c_tax01, c_amt01]
    all_checks.extend(s9_checks)
    s9_result = _compute_stage_result(s9_checks)
    t9_ms = (time.perf_counter() - t0) * 1000
    timings["amounts"] = t9_ms
    var_sign = "+" if variance > 0 else ""
    if s9_result in ("fail", "warn"):
        failing_c = next((c for c in s9_checks if c.result in ("fail", "warn")), None)
        stage9_summary = failing_c.message if failing_c else f"Checked PO balance, tax, and gross variance ({var_sign}${variance:,.2f})."
    else:
        stage9_summary = f"Checked PO balance, tax, and gross variance ({var_sign}${variance:,.2f})."
    emit(9, "finished", {
        "result": s9_result,
        "duration_ms": round(t9_ms, 2),
        "summary": stage9_summary,
        "findings": [c.to_dict() for c in s9_checks],
    }, duration_ms=t9_ms)

    # ── Stage 10: Policy ────────────────────────────────────────────────
    t0 = time.perf_counter()
    emit(10, "started", {"stage": STAGE_NAMES[10]})

    c_pol01 = check_pol_01(
        total_amount=extraction.get("total_amount"),
        auto_approve_limit=config.get("auto_approve_limit", 75000.0),
    )

    s10_checks = [c_pol01]
    all_checks.extend(s10_checks)
    s10_result = _compute_stage_result(s10_checks)
    t10_ms = (time.perf_counter() - t0) * 1000
    timings["policy"] = t10_ms
    if s10_result in ("fail", "warn"):
        failing_c = next((c for c in s10_checks if c.result in ("fail", "warn")), None)
        stage10_summary = failing_c.message if failing_c else "Policy check flagged manager sign-off required."
    else:
        stage10_summary = f"Verified gross against ${config.get('auto_approve_limit', 75000):,.2f} auto-approve limit."
    emit(10, "finished", {
        "result": s10_result,
        "duration_ms": round(t10_ms, 2),
        "summary": stage10_summary,
        "findings": [c.to_dict() for c in s10_checks],
    }, duration_ms=t10_ms)

    # ── Stage 11: Decide and Explain ────────────────────────────────────
    t0 = time.perf_counter()
    emit(11, "started", {"stage": STAGE_NAMES[11]})

    missing_fields = extraction.get("missing_fields") or []
    if c_ext01.result == "fail" and c_ext01.evidence.get("missing_fields"):
        missing_fields = list(set(missing_fields + c_ext01.evidence["missing_fields"]))

    decision = decide(
        checks=all_checks,
        vendor=matched_vendor,
        po=matched_po,
        invoice_date=extraction.get("invoice_date"),
        due_date=extraction.get("due_date"),
        total_amount=extraction.get("total_amount"),
        variance=variance,
        suggested_po=suggested_po,
        missing_fields=missing_fields,
        run_id=run_id,
    )

    t11_ms = (time.perf_counter() - t0) * 1000
    timings["decide"] = t11_ms
    stage11_summary = f"Decision: {decision['status']}. {decision['headline']}"
    dec_result = "pass" if decision["status"] == "APPROVED" else ("warn" if decision["status"] == "NEEDS_REVIEW" else "fail")
    emit(11, "finished", {
        "result": dec_result,
        "duration_ms": round(t11_ms, 2),
        "summary": stage11_summary,
        "findings": decision,
    }, duration_ms=t11_ms)

    # ── Stage 12: Act ───────────────────────────────────────────────────
    t0 = time.perf_counter()
    emit(12, "started", {"stage": STAGE_NAMES[12]})

    ledger_effect: dict[str, Any] = {"posted": False}
    tot_proc_ms = (time.perf_counter() - start_total_time) * 1000

    # Build run record for DB persistence
    run_record = {
        "run_id": run_id,
        "invoice_number": extraction.get("invoice_number"),
        "vendor_id": matched_vendor["id"] if matched_vendor else None,
        "vendor_name": matched_vendor["name"] if matched_vendor else extraction.get("vendor_name"),
        "po_number": matched_po["po_number"] if matched_po else None,
        "status": decision["status"],
        "severity": decision["severity"],
        "headline": decision["headline"],
        "net": inv_net,
        "gross": extraction.get("total_amount"),
        "currency": extraction.get("currency", "USD"),
        "invoice_date": str(extraction.get("invoice_date") or ""),
        "due_date": str(extraction.get("due_date") or ""),
        "pay_by": decision.get("pay_by"),
        "next_action": decision["next_action"],
        "email_draft": decision.get("email_draft"),
        "extraction_json": json.dumps(extraction),
        "processing_time_ms": round(tot_proc_ms, 2),
        "created_at": created_at,
        "seeded": 0,
        "file_hash": file_hash,
        "file_name": filename,
    }

    # Post to ledger ONLY when the status is APPROVED
    if decision["status"] == "APPROVED" and matched_vendor and matched_po:
        ledger_lines_data = []
        for inv_l, po_l in matched_pairs:
            qty = float(inv_l.get("quantity") or 0.0)
            uprice = float(po_l.get("unit_price") or 0.0)
            line_net = float(inv_l.get("line_total") or (qty * uprice))
            ledger_lines_data.append({
                "po_line_number": po_l["line_number"],
                "quantity_invoiced": qty,
                "net_amount": line_net,
            })

        ledger_id = post_to_ledger(
            db,
            invoice_number=extraction.get("invoice_number") or filename,
            vendor_id=matched_vendor["id"],
            po_number=matched_po["po_number"],
            net=inv_net or 0.0,
            gross=float(extraction.get("total_amount") or 0.0),
            invoice_date=str(extraction.get("invoice_date") or as_of.isoformat()),
            posted_at=as_of.isoformat(),
            run_id=run_id,
            lines=ledger_lines_data,
        )
        ledger_effect = {"posted": True, "ledger_id": ledger_id}
    else:
        ledger_effect = {
            "posted": False,
            "reason": f"Status is {decision['status']}, not APPROVED.",
        }

    t12_ms = (time.perf_counter() - t0) * 1000
    timings["act"] = t12_ms
    act_text = "Posted to ledger" if ledger_effect["posted"] else str(decision["next_action"])
    stage12_summary = f"Action: {act_text.rstrip('.')}."

    # Build and return the complete B10 decision record
    checks_data = [c.to_dict() for c in all_checks]
    decision_record = {
        "run_id": run_id,
        "status": decision["status"],
        "severity": decision["severity"],
        "headline": decision["headline"],
        "reasons": decision["reasons"],
        "next_action": decision["next_action"],
        "vendor": {
            "id": matched_vendor["id"] if matched_vendor else None,
            "name": matched_vendor["name"] if matched_vendor else extraction.get("vendor_name"),
            "tax_id": matched_vendor.get("tax_id") if matched_vendor else None,
            "status": matched_vendor.get("status") if matched_vendor else "unknown",
            "callback_phone": matched_vendor.get("callback_phone") if matched_vendor else None,
        },
        "po": {
            "po_number": matched_po["po_number"] if matched_po else None,
            "remaining_net": rem_po_net,
            "tax_rate": po_rate if matched_po else None,
        },
        "gross": extraction.get("total_amount"),
        "net": inv_net,
        "currency": extraction.get("currency", "USD"),
        "pay_by": decision.get("pay_by"),
        "checks": checks_data,
        "extraction": {
            "fields": extraction,
            "source": ext_source,
            "model": ext_model,
            "is_scanned": is_scanned,
        },
        "timings": {
            "total_ms": round(tot_proc_ms, 2),
            **timings,
        },
        "email_draft": decision.get("email_draft"),
        "ledger_effect": ledger_effect,
        "stage_events": stage_events,
    }

    # Save run record and checks to DB BEFORE emitting stage 12 finished
    if db is not None:
        save_run(db, run_record, checks_data)

    emit(12, "finished", {
        "result": "pass",
        "duration_ms": round(t12_ms, 2),
        "summary": stage12_summary,
        "findings": ledger_effect,
    }, duration_ms=t12_ms)

    return decision_record
