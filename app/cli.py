"""Command Line Interface for running invoice processing pipeline.

Usage:
    python -m app.cli run <path_to_invoice_pdf>
"""

import argparse
import hashlib
import json
from pathlib import Path
import sys

from app.db import get_connection, create_tables, is_db_seeded
from app.seed import seed_all
from app.pipeline import run_pipeline

BASE_DIR = Path(__file__).resolve().parent.parent
SAMPLES_DIR = BASE_DIR / "samples"
FIXTURES_DIR = BASE_DIR / "fixtures" / "extractions"


def fixture_extract_fn(file_bytes: bytes, filename: str, file_hash: str) -> dict:
    """Extract fields using sample manifest + fixture extractions."""
    manifest_path = SAMPLES_DIR / "manifest.json"
    scenario_id = None

    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            scenario_id = manifest.get(file_hash)
        except Exception:
            pass

    # Fallback to filename matching if manifest hash missed
    if not scenario_id:
        fn_upper = filename.upper()
        for candidate in ["S0", "S1", "E1A", "E1B", "E2", "E3", "E4"]:
            if candidate in fn_upper:
                scenario_id = candidate.replace("E1A", "E1a").replace("E1B", "E1b")
                break

    if scenario_id:
        fixture_file = FIXTURES_DIR / f"{scenario_id}.json"
        if fixture_file.exists():
            data = json.loads(fixture_file.read_text(encoding="utf-8"))
            return {
                "fields": data,
                "source": "fixture",
                "model": f"fixture-{scenario_id}",
            }

    # Generic fallback if no fixture found
    return {
        "fields": {
            "vendor_name": Path(filename).stem,
            "invoice_number": None,
            "invoice_date": None,
            "total_amount": None,
            "currency": "USD",
            "missing_fields": ["invoice_number", "invoice_date", "total_amount"],
        },
        "source": "fallback",
        "model": "none",
    }


def cli_on_event(evt: dict) -> None:
    """Print stage event to console."""
    if evt["event_type"] == "finished":
        res = evt["data"].get("result", "pass").upper()
        icons = {"PASS": "✓", "WARN": "⚠️", "FAIL": "✗", "SKIP": "○"}
        icon = icons.get(res, "•")
        stage_num = evt["stage_number"]
        stage_name = evt["stage_name"]
        ms = evt["duration_ms"]
        summary = evt["data"].get("summary", "")
        print(f"[{stage_num:2d}/12] {icon} {stage_name:<20} ({ms:5.1f}ms) — {summary}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Invoice Processing Pipeline CLI")
    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser("run", help="Run invoice processing on a PDF file")
    run_parser.add_argument("file_path", help="Path to PDF or image file")

    args = parser.parse_args()

    if args.command == "run":
        fpath = Path(args.file_path)
        if not fpath.exists():
            print(f"Error: File not found: {fpath}", file=sys.stderr)
            sys.exit(1)

        file_bytes = fpath.read_bytes()
        conn = get_connection()
        create_tables(conn)
        if not is_db_seeded(conn):
            seed_all(conn)

        print("\n" + "=" * 70)
        print(f"RUNNING INVOICE PROCESSING PIPELINE: {fpath.name}")
        print("=" * 70 + "\n")

        record = run_pipeline(
            file_bytes=file_bytes,
            filename=fpath.name,
            extract_fn=fixture_extract_fn,
            db=conn,
            on_event=cli_on_event,
        )

        print("\n" + "=" * 70)
        print("DECISION RECORD")
        print("=" * 70)
        print(f"Run ID:       {record['run_id']}")
        print(f"Status:       {record['status']}")
        print(f"Severity:     {record['severity']}")
        print(f"Headline:     {record['headline']}")
        print(f"Next Action:  {record['next_action']}")
        print(f"Vendor:       {record['vendor'].get('name')} (ID: {record['vendor'].get('id')})")
        print(f"PO:           {record['po'].get('po_number')}")
        print(f"Gross:        ${record.get('gross', 0) or 0:,.2f} {record.get('currency', 'USD')}")
        print(f"Net:          ${record.get('net', 0) or 0:,.2f}")
        print(f"Total Time:   {record['timings']['total_ms']:.1f} ms")

        print("\nReasons:")
        for r in record["reasons"]:
            print(f"  • {r}")

        if record.get("email_draft"):
            print("\nDraft / Call Script:")
            print("-" * 50)
            print(record["email_draft"])
            print("-" * 50)

        print(f"\nLedger Effect: {record['ledger_effect']}")
        print("=" * 70 + "\n")


if __name__ == "__main__":
    main()
