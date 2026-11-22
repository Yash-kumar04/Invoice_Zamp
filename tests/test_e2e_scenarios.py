"""End-to-end scenario tests for invoice processing pipeline.

Tests run the 7 sample PDFs in demo order against a seeded DB:
Reset -> S0 -> S1 -> E1a -> E1b -> E2 -> E4 -> E3
Asserts exact status, fired rule IDs, and key financial numbers per SPEC.md B9.
Also tests E1b run alone on fresh data, ledger posting invariants, and 12-stage event sequencing.
"""

from pathlib import Path
import pytest

from app.db import (
    get_connection,
    create_tables,
    get_po_remaining_net,
    get_po_line_remaining_qty,
    get_ledger_entries,
)
from app.seed import seed_all
from app.pipeline import run_pipeline
from app.cli import fixture_extract_fn
from scenarios.scenarios import SCENARIOS, SAMPLES_DIR


@pytest.fixture(scope="module")
def demo_suite():
    """Execute the 7 demo scenarios in exact demo order on a fresh seeded DB."""
    conn = get_connection(":memory:")
    create_tables(conn)
    seed_all(conn)

    order = ["S0", "S1", "E1a", "E1b", "E2", "E4", "E3"]
    results = {}

    for s_id in order:
        s = SCENARIOS[s_id]
        pdf_path = SAMPLES_DIR / s["file_name"]
        file_bytes = pdf_path.read_bytes()

        events_captured = []
        rec = run_pipeline(
            file_bytes=file_bytes,
            filename=s["file_name"],
            extract_fn=fixture_extract_fn,
            db=conn,
            on_event=lambda e: events_captured.append(e),
        )
        rec["captured_events"] = events_captured
        results[s_id] = rec

    return {"conn": conn, "results": results}


class TestDemoOrderScenarios:
    def test_s0_approved_variance_zero(self, demo_suite):
        """S0: Northwind INV-20831, PO-7301 -> APPROVED, variance 0.00, 3-way OK."""
        res = demo_suite["results"]["S0"]
        assert res["status"] == "APPROVED"
        assert res["severity"] == "LOW"
        assert res["ledger_effect"]["posted"] is True
        assert res["gross"] == 46170.00
        assert res["net"] == 42750.00

        # Check all checks pass
        failing_checks = [c for c in res["checks"] if c["result"] == "fail"]
        assert len(failing_checks) == 0

        # Check headline
        assert "Approved." in res["headline"]
        assert "variance $0.00" in res["headline"]

    def test_s1_approved_with_three_notes(self, demo_suite):
        """S1: BrightPath INV-BP-2291, PO-7315 -> APPROVED with 3 notes:

        AMT-01 note +30.00 (+0.23%) against limit 100.00; tax inferred; bundled line.
        """
        res = demo_suite["results"]["S1"]
        assert res["status"] == "APPROVED"
        assert res["ledger_effect"]["posted"] is True
        assert res["gross"] == 13230.00

        # 3 warning checks
        warning_check_ids = {c["check_id"] for c in res["checks"] if c["result"] == "warn"}
        assert "AMT-01" in warning_check_ids
        assert "TAX-01" in warning_check_ids
        assert "LINE-01" in warning_check_ids

        # Check AMT-01 variance details
        amt_check = next(c for c in res["checks"] if c["check_id"] == "AMT-01")
        assert amt_check["evidence"]["variance"] == 30.00
        assert amt_check["evidence"]["tolerance"] == 100.00

    def test_e1a_approved_and_consumes_po_balance(self, demo_suite):
        """E1a: Apex APX-1055, PO-7310 -> APPROVED.

        PO net remaining 30,000.00 before and 4,000.00 after; pumps available 8 before, 0 after.
        """
        res = demo_suite["results"]["E1a"]
        conn = demo_suite["conn"]
        assert res["status"] == "APPROVED"
        assert res["ledger_effect"]["posted"] is True
        assert res["gross"] == 28080.00
        assert res["net"] == 26000.00

        # After E1a: PO-7310 net remaining is exactly 4,000.00
        rem_net = get_po_remaining_net(conn, "PO-7310")
        assert rem_net == pytest.approx(4000.00)

        # Unbilled pumps remaining is 0 (18 received - 10 APX-1042 - 8 APX-1055 = 0)
        rem_pumps = get_po_line_remaining_qty(conn, "PO-7310", 1)
        assert rem_pumps == pytest.approx(0.0)

    def test_e1b_needs_review_after_e1a(self, demo_suite):
        """E1b: Apex APX-1061, PO-7310 (run after E1a) -> NEEDS_REVIEW.

        BAL-01: 8,000.00 asked, 4,000.00 left; 3W-01: 0 pumps received and unbilled.
        """
        res = demo_suite["results"]["E1b"]
        assert res["status"] == "NEEDS_REVIEW"
        assert res["ledger_effect"]["posted"] is False
        assert res["next_action"] == "queue_for_review"

        failing_check_ids = {c["check_id"] for c in res["checks"] if c["result"] == "fail"}
        assert "BAL-01" in failing_check_ids
        assert "3W-01" in failing_check_ids

        # BAL-01: over by 4,000.00
        bal_check = next(c for c in res["checks"] if c["check_id"] == "BAL-01")
        assert bal_check["evidence"]["invoice_net"] == 8000.00
        assert bal_check["evidence"]["remaining_net"] == pytest.approx(4000.00)
        assert bal_check["evidence"]["overage"] == pytest.approx(4000.00)

        # 3W-01: 4 asked, 0 available
        tw_check = next(c for c in res["checks"] if c["check_id"] == "3W-01")
        assert tw_check["evidence"]["overages"][0]["invoiced_qty"] == 4.0
        assert tw_check["evidence"]["overages"][0]["available_qty"] == 0.0

    def test_e2_rejected_duplicate_in_disguise(self, demo_suite):
        """E2: Crestline printed CR 457, ref PO7322 -> REJECTED.

        DUP-01 normalised key cr457 points to posted CR-000457 run; PO-03 also fails (0 balance).
        """
        res = demo_suite["results"]["E2"]
        assert res["status"] == "REJECTED"
        assert res["ledger_effect"]["posted"] is False

        failing_check_ids = {c["check_id"] for c in res["checks"] if c["result"] == "fail"}
        assert "DUP-01" in failing_check_ids
        assert "PO-03" in failing_check_ids

        dup_check = next(c for c in res["checks"] if c["check_id"] == "DUP-01")
        assert dup_check["evidence"]["matching_invoice"] == "CR-000457"
        assert dup_check["evidence"]["matching_run_id"] == "SEED-02"

    def test_e4_needs_review_critical_bank_change(self, demo_suite):
        """E4: BrightPath INV-BP-2304, PO-7316 -> NEEDS_REVIEW, CRITICAL.

        VEN-04 fail, VEN-05 warn, every numeric check passes; next action is to call +1-555-0103.
        """
        res = demo_suite["results"]["E4"]
        assert res["status"] == "NEEDS_REVIEW"
        assert res["severity"] == "CRITICAL"
        assert res["ledger_effect"]["posted"] is False

        # Numeric checks pass
        assert next(c for c in res["checks"] if c["check_id"] == "BAL-01")["result"] == "pass"
        assert next(c for c in res["checks"] if c["check_id"] == "TAX-01")["result"] == "pass"
        assert next(c for c in res["checks"] if c["check_id"] == "AMT-01")["result"] == "pass"

        # VEN-04 failed with CRITICAL severity
        ven04 = next(c for c in res["checks"] if c["check_id"] == "VEN-04")
        assert ven04["result"] == "fail"
        assert ven04["severity"] == "CRITICAL"

        # VEN-05 warned
        ven05 = next(c for c in res["checks"] if c["check_id"] == "VEN-05")
        assert ven05["result"] == "warn"

        # Next action and call script contain +1-555-0103 from master record
        assert "+1-555-0103" in res["next_action"]
        call_script = res["email_draft"]
        assert "INTERNAL CALL SCRIPT" in call_script
        assert "+1-555-0103" in call_script
        # Must NOT call the contact on the invoice
        assert "ap@brightpath-billing.example" not in res["next_action"]

    def test_e3_needs_info_incomplete_scan(self, demo_suite):
        """E3: Meridian phone photo -> NEEDS_INFO.

        EXT-01 missing invoice number & date; PO-01 suggests PO-7318; email asks for missing items; no ledger row.
        """
        res = demo_suite["results"]["E3"]
        conn = demo_suite["conn"]
        assert res["status"] == "NEEDS_INFO"
        assert res["ledger_effect"]["posted"] is False

        # EXT-01 failed
        ext01 = next(c for c in res["checks"] if c["check_id"] == "EXT-01")
        assert ext01["result"] == "fail"
        assert "invoice_number" in ext01["evidence"]["missing_fields"]
        assert "invoice_date" in ext01["evidence"]["missing_fields"]

        # PO-01 suggests PO-7318
        po01 = next(c for c in res["checks"] if c["check_id"] == "PO-01")
        assert po01["evidence"]["suggested_po"] == "PO-7318"

        # Vendor email draft lists missing items and suggested PO
        email = res["email_draft"]
        assert email is not None
        assert "PO-7318" in email
        assert "Invoice Number" in email
        assert "Invoice Date" in email

        # Produced no ledger row for Meridian
        meridian_ledger = get_ledger_entries(conn, vendor_id="V-004")
        assert len(meridian_ledger) == 0


class TestStandaloneE1b:
    def test_e1b_run_alone_is_approved(self):
        """Test that E1b run alone on freshly seeded data (before E1a) is APPROVED."""
        conn = get_connection(":memory:")
        create_tables(conn)
        seed_all(conn)

        s = SCENARIOS["E1b"]
        pdf_path = SAMPLES_DIR / s["file_name"]
        rec = run_pipeline(
            file_bytes=pdf_path.read_bytes(),
            filename=s["file_name"],
            extract_fn=fixture_extract_fn,
            db=conn,
        )

        assert rec["status"] == "APPROVED"
        assert rec["ledger_effect"]["posted"] is True

        # BAL-01 passed (8,000 <= 30,000 remaining)
        bal01 = next(c for c in rec["checks"] if c["check_id"] == "BAL-01")
        assert bal01["result"] == "pass"

        # 3W-01 passed (4 pumps <= 8 unbilled received)
        tw01 = next(c for c in rec["checks"] if c["check_id"] == "3W-01")
        assert tw01["result"] == "pass"

        # Remaining net after standalone E1b: 30,000 - 8,000 = 22,000.00
        assert get_po_remaining_net(conn, "PO-7310") == pytest.approx(22000.00)


class TestLedgerInvariants:
    def test_only_approved_invoices_post_to_ledger(self, demo_suite):
        """Assert that across the whole demo run, only APPROVED invoices posted to ledger."""
        conn = demo_suite["conn"]
        results = demo_suite["results"]

        for s_id, rec in results.items():
            if rec["status"] == "APPROVED":
                assert rec["ledger_effect"]["posted"] is True
            else:
                assert rec["ledger_effect"]["posted"] is False

        # In seeded ledger, there were 2 entries (APX-1042 and CR-000457).
        # In demo run, S0, S1, and E1a were approved (3 approved).
        # Total in ledger must be exactly 2 + 3 = 5!
        all_ledger = get_ledger_entries(conn)
        assert len(all_ledger) == 5


class TestStageEventsSequencing:
    def test_all_12_stages_emitted_in_exact_order(self, demo_suite):
        """For every run, exactly 24 stage events (12 started, 12 finished) are emitted in order."""
        for s_id, rec in demo_suite["results"].items():
            events = rec["captured_events"]
            assert len(events) == 24, f"Run {s_id} did not emit 24 events"

            # Check sequence of stage numbers: [1, 1, 2, 2, ..., 12, 12]
            expected_stages = [1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6, 7, 7, 8, 8, 9, 9, 10, 10, 11, 11, 12, 12]
            actual_stages = [e["stage_number"] for e in events]
            assert actual_stages == expected_stages

            # Check event types alternate started, finished
            expected_types = ["started", "finished"] * 12
            actual_types = [e["event_type"] for e in events]
            assert actual_types == expected_types

            # Verify every finished event has a valid result
            for e in events:
                if e["event_type"] == "finished":
                    assert e["data"]["result"] in ("pass", "warn", "fail", "skip")
                    assert "summary" in e["data"]
                    assert e["duration_ms"] >= 0.0
