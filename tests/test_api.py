"""API endpoint tests for FastAPI server per SPEC B12."""

import io
import pytest
from fastapi.testclient import TestClient

from app.main import app, rate_limiter
from app.db import get_connection, create_tables
from app.seed import seed_all


@pytest.fixture(scope="module")
def client():
    # Use memory or temp db for tests
    conn = get_connection(":memory:")
    create_tables(conn)
    seed_all(conn)
    with TestClient(app) as c:
        yield c


def test_healthz(client):
    res = client.get("/healthz")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


def test_get_config(client):
    res = client.get("/api/config")
    assert res.status_code == 200
    data = res.json()
    assert "rules" in data
    assert "as_of_date" in data
    assert "models" in data
    assert data["rules"]["currency"] == "USD"


def test_get_master(client):
    res = client.get("/api/master")
    assert res.status_code == 200
    data = res.json()
    assert "vendors" in data
    assert "purchase_orders" in data
    assert len(data["vendors"]) >= 6
    assert len(data["purchase_orders"]) >= 6


def test_get_samples(client):
    res = client.get("/api/samples")
    assert res.status_code == 200
    data = res.json()
    assert "samples" in data
    sample_ids = [s["id"] for s in data["samples"]]
    assert sample_ids == ["S0", "S1", "E1a", "E1b", "E2", "E4", "E3"]


def test_get_kpis(client):
    res = client.get("/api/kpis")
    assert res.status_code == 200
    kpis = res.json()
    assert "invoices_processed" in kpis
    assert "touchless_rate_pct" in kpis
    assert "amount_at_risk_caught" in kpis
    assert "top_rules_fired" in kpis


def test_reset_endpoint(client):
    res = client.post("/api/reset")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


def test_upload_invalid_magic_bytes(client):
    # Non-PDF, non-image text file
    fake_file = io.BytesIO(b"This is just plain text, not a valid PDF or image file.")
    res = client.post(
        "/api/runs",
        files={"file": ("fake.pdf", fake_file, "application/pdf")},
    )
    assert res.status_code == 400
    assert "Invalid file format" in res.json()["detail"]


def test_upload_file_too_large(client):
    # File larger than 10MB
    large_file = io.BytesIO(b"%PDF-1.4 " + b"0" * (11 * 1024 * 1024))
    res = client.post(
        "/api/runs",
        files={"file": ("large.pdf", large_file, "application/pdf")},
    )
    assert res.status_code == 400
    assert "exceeds the 10 MB maximum limit" in res.json()["detail"]


def test_rate_limiting():
    limiter = rate_limiter
    ip = "192.168.1.99"
    # Consume 20 requests
    for _ in range(20):
        assert limiter.is_allowed(ip) is True
    # 21st request should be rejected
    assert limiter.is_allowed(ip) is False


def test_review_action(client):
    # Get any existing run
    runs_res = client.get("/api/runs")
    assert runs_res.status_code == 200
    runs = runs_res.json()["runs"]
    if runs:
        target_run_id = runs[0]["run_id"]
        # Review reject
        rev_res = client.post(
            f"/api/runs/{target_run_id}/review",
            json={"action": "reject", "note": "Flagged by compliance auditor"},
        )
        assert rev_res.status_code == 200
        assert rev_res.json()["status"] == "REJECTED"


def test_forced_extraction_failure_ends_in_error(client):
    """Regression test 1: Forced extraction failure on PDF outside manifest persists terminal ERROR."""
    from unittest.mock import patch
    import time
    import fitz

    # Create a fresh 1-page PDF with unique content not in manifest.json
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 100), "Random Non-Manifest Invoice Content 987654321")
    pdf_bytes = doc.tobytes()
    doc.close()

    with patch("app.main.extract_with_gemini", side_effect=RuntimeError("Provider 503 Service Unavailable")):
        res = client.post(
            "/api/runs",
            files={"file": ("unmanifested_invoice.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
        )
        assert res.status_code == 200
        run_id = res.json()["run_id"]

        # Poll for async completion
        final_detail = None
        for _ in range(30):
            time.sleep(0.1)
            det_res = client.get(f"/api/runs/{run_id}")
            if det_res.status_code == 200 and det_res.json().get("status") != "RUNNING":
                final_detail = det_res.json()
                break

        assert final_detail is not None, "Run did not complete in time"
        assert final_detail["status"] == "ERROR"
        assert final_detail["severity"] == "HIGH"
        assert "retry" in final_detail["headline"].lower()
        assert final_detail["next_action"] == "retry_run"
        assert final_detail["extraction"]["source"] == "failed"
        assert final_detail["ledger_effect"]["posted"] is False

        # Verify no ledger effect
        conn = get_connection()
        try:
            ledger_entry = conn.execute("SELECT id FROM ledger WHERE run_id = ?", (run_id,)).fetchone()
            assert ledger_entry is None, "Failed run must not post to ledger"
        finally:
            conn.close()


def test_cached_run_preserves_cached_label_when_reopened(client):
    """Regression test 2: Cached extraction preserves 'cached' and model fixture label across reloads."""
    from unittest.mock import patch
    import time

    # Run E4 with live Gemini failing so it falls back to cached fixture
    with patch("app.extract.get_model_chain", return_value=["dummy-model"]):
        with patch("google.genai.Client", side_effect=Exception("Simulated API failure")):
            res = client.post("/api/runs", data={"sample_id": "E4"})
            assert res.status_code == 200
            run_id = res.json()["run_id"]

            final_detail = None
            for _ in range(50):
                time.sleep(0.1)
                det_res = client.get(f"/api/runs/{run_id}")
                if det_res.status_code == 200 and det_res.json().get("status") != "RUNNING":
                    final_detail = det_res.json()
                    break

            assert final_detail is not None
            assert final_detail["status"] == "NEEDS_REVIEW"
            # Verify source and model are cached, NOT live / gemini
            assert final_detail["extraction"]["source"] == "cached"
            assert final_detail["extraction"]["model"] == "fixture-E4"

            # Re-fetch run detail to verify reopening preserves the cached source
            refetched = client.get(f"/api/runs/{run_id}").json()
            assert refetched["extraction"]["source"] == "cached"
            assert refetched["extraction"]["model"] == "fixture-E4"

            # List runs endpoint also preserves decision
            list_res = client.get("/api/runs").json()
            matching = next((r for r in list_res["runs"] if r["run_id"] == run_id), None)
            assert matching is not None
            assert matching.get("decision", {}).get("extraction", {}).get("source") == "cached"


def test_older_run_with_no_recorded_source_shows_source_unavailable(client):
    """Regression test 3: Runs without recorded source/model return 'Source unavailable'."""
    from app.db import save_run

    conn = get_connection()
    try:
        run_id = "RUN-LEGACY-001"
        save_run(conn, {
            "run_id": run_id,
            "invoice_number": "INV-LEGACY",
            "vendor_id": "V-001",
            "status": "APPROVED",
            "extraction_json": '{"invoice_number": "INV-LEGACY", "total_amount": 100.0}',
            "decision_json": None,
        })
    finally:
        conn.close()

    det_res = client.get(f"/api/runs/{run_id}")
    assert det_res.status_code == 200
    detail = det_res.json()
    assert detail["extraction"]["source"] == "Source unavailable"
    assert detail["extraction"]["model"] == "Source unavailable"

