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
