"""Live check script: runs all 7 sample PDFs through the real Gemini path in demo order.

Verifies:
1. Resets demo data first.
2. Executes S0, S1, E1a, E1b, E2, E4, E3 via extract_with_gemini.
3. Prints actual vs expected status, serving model, and any extracted field differences against fixtures.
4. Exits 0 if all 7 match expected status.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

# Add project root to sys.path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db import create_tables, get_connection
from app.extract import extract_with_gemini
from app.pipeline import run_pipeline
from app.seed import reset_demo_data, seed_all
from scenarios.scenarios import SCENARIOS

load_dotenv()

DEMO_ORDER = ["S0", "S1", "E1a", "E1b", "E2", "E4", "E3"]
EXPECTED_STATUSES = {
    "S0": "APPROVED",
    "S1": "APPROVED",
    "E1a": "APPROVED",
    "E1b": "NEEDS_REVIEW",
    "E2": "REJECTED",
    "E4": "NEEDS_REVIEW",
    "E3": "NEEDS_INFO",
}


def find_field_differences(actual: dict[str, Any], expected: dict[str, Any]) -> list[str]:
    """Compare extracted fields with fixture extraction and list differences."""
    diffs = []
    keys_to_compare = [
        "vendor_name",
        "vendor_tax_id",
        "vendor_email",
        "bank_account_last4",
        "invoice_number",
        "invoice_date",
        "total_amount",
        "currency",
        "po_reference",
        "other_references",
    ]
    for k in keys_to_compare:
        act_val = actual.get(k)
        exp_val = expected.get(k)

        # Normalize empty strings to None
        if isinstance(act_val, str) and not act_val.strip():
            act_val = None
        if isinstance(exp_val, str) and not exp_val.strip():
            exp_val = None

        if act_val != exp_val:
            diffs.append(f"{k}: actual='{act_val}' vs expected='{exp_val}'")
    return diffs


def main():
    print("=" * 80)
    print("RUNNING LIVE CHECK: 7 DEMO INVOICES THROUGH REAL GEMINI API")
    print("=" * 80)

    # 1. Reset DB
    conn = get_connection(":memory:")
    create_tables(conn)
    seed_all(conn)
    reset_demo_data(conn)
    print("✓ Demo database reset successfully.\n")

    results = []
    all_matched = True

    for s_id in DEMO_ORDER:
        scenario = SCENARIOS[s_id]
        filename = scenario["file_name"]
        pdf_path = ROOT / "samples" / filename
        if not pdf_path.exists():
            print(f"ERROR: Sample file {pdf_path} not found!")
            sys.exit(1)

        file_bytes = pdf_path.read_bytes()
        expected_status = EXPECTED_STATUSES[s_id]

        print(f"[{s_id}] Running {filename} through real Gemini extraction...")
        rec = run_pipeline(
            file_bytes=file_bytes,
            filename=filename,
            extract_fn=extract_with_gemini,
            db=conn,
            on_event=None,
        )

        actual_status = rec["status"]
        matched = (actual_status == expected_status)
        if not matched:
            all_matched = False

        extraction = rec.get("extraction", {})
        ext_fields = extraction.get("fields", {})
        serving_model = extraction.get("model", "unknown")
        ext_source = extraction.get("source", "unknown")

        # Load fixture for comparison
        fixture_path = ROOT / "fixtures" / "extractions" / f"{s_id}.json"
        diffs = []
        if fixture_path.exists():
            fixture_data = json.loads(fixture_path.read_text(encoding="utf-8"))
            diffs = find_field_differences(ext_fields, fixture_data)

        results.append({
            "id": s_id,
            "filename": filename,
            "expected": expected_status,
            "actual": actual_status,
            "matched": matched,
            "model": f"{serving_model} ({ext_source})",
            "diffs": diffs,
            "headline": rec.get("headline", ""),
            "total_ms": rec.get("timings", {}).get("extract", 0.0),
        })

    # Print summary table
    print("\n" + "=" * 80)
    print("LIVE CHECK RESULTS SUMMARY")
    print("=" * 80)
    print(f"{'ID':<5} | {'Expected':<12} | {'Actual':<12} | {'Match':<6} | {'Serving Model':<30}")
    print("-" * 80)

    for r in results:
        status_sym = "PASS" if r["matched"] else "FAIL"
        print(f"{r['id']:<5} | {r['expected']:<12} | {r['actual']:<12} | {status_sym:<6} | {r['model']:<30}")
        if r["diffs"]:
            print(f"      Field differences: {'; '.join(r['diffs'])}")
        else:
            print("      Field differences: None (exact match with fixture)")

    print("=" * 80)
    if all_matched:
        print("ALL 7 SCENARIOS MATCHED EXPECTED STATUSES VIA LIVE GEMINI!")
        sys.exit(0)
    else:
        print("FAILURE: Some scenarios did not match expected statuses.")
        sys.exit(1)


if __name__ == "__main__":
    main()
