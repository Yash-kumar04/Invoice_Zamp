"""Tests for sample invoice generator, PDF text layers, and B7 fixtures."""

import json
from pathlib import Path
import pymupdf as fitz
import pytest

from scenarios.scenarios import (
    SCENARIOS,
    generate_all,
    get_file_sha256,
    SAMPLES_DIR,
    FIXTURES_DIR,
)

B7_REQUIRED_FIELDS = [
    "vendor_name",
    "vendor_tax_id",
    "vendor_email",
    "bank_account_last4",
    "bank_iban",
    "invoice_number",
    "invoice_date",
    "invoice_date_raw",
    "due_date",
    "due_date_raw",
    "currency",
    "po_reference",
    "other_references",
    "line_items",
    "subtotal",
    "tax_amount",
    "tax_rate",
    "tax_mode",
    "discount",
    "total_amount",
    "payment_terms",
    "page_notes",
    "evidence",
    "document_quality",
    "missing_fields",
]


class TestScenarioPDFs:
    @classmethod
    def setup_class(cls):
        """Ensure all samples and fixtures are generated."""
        generate_all()

    def test_s0_has_text_layer_and_invoice_number(self):
        pdf_path = SAMPLES_DIR / SCENARIOS["S0"]["file_name"]
        assert pdf_path.exists()
        with fitz.open(pdf_path) as doc:
            page = doc[0]
            text = page.get_text()
            assert "INV-20831" in text
            assert "Northwind Traders Ltd" in text
            assert "46,170.00" in text

    def test_s1_has_text_layer_and_bundled_notice(self):
        pdf_path = SAMPLES_DIR / SCENARIOS["S1"]["file_name"]
        assert pdf_path.exists()
        with fitz.open(pdf_path) as doc:
            page = doc[0]
            text = page.get_text()
            assert "INV-BP-2291" in text
            assert "13,230.00" in text
            assert "BrightPath IT Services" in text

    def test_e1a_has_text_layer(self):
        pdf_path = SAMPLES_DIR / SCENARIOS["E1a"]["file_name"]
        with fitz.open(pdf_path) as doc:
            text = doc[0].get_text()
            assert "APX-1055" in text
            assert "28,080.00" in text

    def test_e1b_has_text_layer(self):
        pdf_path = SAMPLES_DIR / SCENARIOS["E1b"]["file_name"]
        with fitz.open(pdf_path) as doc:
            text = doc[0].get_text()
            assert "APX-1061" in text
            assert "8,640.00" in text

    def test_e4_has_text_layer_and_changed_bank_notice(self):
        pdf_path = SAMPLES_DIR / SCENARIOS["E4"]["file_name"]
        with fitz.open(pdf_path) as doc:
            text = doc[0].get_text()
            assert "INV-BP-2304" in text
            assert "9027" in text
            assert "ap@brightpath-billing.example" in text

    def test_e2_is_image_only_with_empty_text_layer(self):
        """E2 must be an image-only PDF with NO text layer."""
        pdf_path = SAMPLES_DIR / SCENARIOS["E2"]["file_name"]
        assert pdf_path.exists()
        with fitz.open(pdf_path) as doc:
            page = doc[0]
            text = page.get_text().strip()
            assert text == "", f"E2 should have empty text layer, but got: {text[:50]}"
            # Ensure image is present
            image_list = page.get_images()
            assert len(image_list) >= 1

    def test_e3_is_image_only_with_empty_text_layer(self):
        """E3 must be an image-only PDF with NO text layer."""
        pdf_path = SAMPLES_DIR / SCENARIOS["E3"]["file_name"]
        assert pdf_path.exists()
        with fitz.open(pdf_path) as doc:
            page = doc[0]
            text = page.get_text().strip()
            assert text == "", f"E3 should have empty text layer, but got: {text[:50]}"
            # Ensure image is present
            image_list = page.get_images()
            assert len(image_list) >= 1


class TestExtractionFixtures:
    @pytest.mark.parametrize("scenario_id", ["S0", "S1", "E1a", "E1b", "E2", "E3", "E4"])
    def test_fixture_matches_b7_schema(self, scenario_id):
        fixture_path = FIXTURES_DIR / f"{scenario_id}.json"
        assert fixture_path.exists(), f"Fixture {fixture_path} does not exist"
        data = json.loads(fixture_path.read_text(encoding="utf-8"))

        for req_field in B7_REQUIRED_FIELDS:
            assert req_field in data, f"Missing B7 field '{req_field}' in {scenario_id}.json"

        assert isinstance(data["line_items"], list)
        assert isinstance(data["other_references"], list)
        assert isinstance(data["missing_fields"], list)
        assert isinstance(data["evidence"], dict)
        assert data["currency"] == "USD"
        assert data["document_quality"] in ("clean", "scanned_ok", "scanned_poor")

    def test_e3_null_fields_and_quality(self):
        e3_data = json.loads((FIXTURES_DIR / "E3.json").read_text(encoding="utf-8"))
        assert e3_data["invoice_number"] is None
        assert e3_data["invoice_date"] is None
        assert e3_data["po_reference"] is None
        assert e3_data["document_quality"] == "scanned_poor"
        assert "invoice_number" in e3_data["missing_fields"]
        assert "invoice_date" in e3_data["missing_fields"]
        assert e3_data["other_references"] == ["Quote Q-8890"]

    def test_e2_printed_fields(self):
        e2_data = json.loads((FIXTURES_DIR / "E2.json").read_text(encoding="utf-8"))
        assert e2_data["invoice_number"] == "CR 457"
        assert e2_data["po_reference"] == "PO7322"
        assert e2_data["document_quality"] == "scanned_ok"

    def test_s1_included_tax(self):
        s1_data = json.loads((FIXTURES_DIR / "S1.json").read_text(encoding="utf-8"))
        assert s1_data["tax_mode"] == "included"
        assert s1_data["total_amount"] == 13230.00
        assert len(s1_data["line_items"]) == 1

    @pytest.mark.parametrize("scenario_id", ["S0", "S1", "E1a", "E1b", "E2", "E3", "E4"])
    def test_fixture_arithmetic_consistency(self, scenario_id):
        data = json.loads((FIXTURES_DIR / f"{scenario_id}.json").read_text(encoding="utf-8"))
        lines_sum = sum(item["line_total"] for item in data["line_items"])
        tax = data["tax_amount"] or 0.0
        discount = data["discount"] or 0.0
        total = data["total_amount"]

        if data["tax_mode"] == "included":
            assert lines_sum == pytest.approx(total, abs=0.02)
        else:
            calc_total = lines_sum + tax - discount
            assert calc_total == pytest.approx(total, abs=0.02)


class TestDeterminismAndManifest:
    def test_manifest_maps_all_scenarios(self):
        manifest_path = SAMPLES_DIR / "manifest.json"
        assert manifest_path.exists()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert len(manifest) == 7
        assert set(manifest.values()) == {"S0", "S1", "E1a", "E1b", "E2", "E3", "E4"}

    def test_regeneration_gives_identical_hashes(self):
        """Regenerating twice produces identical SHA-256 hashes for all files."""
        # Initial hashes
        first_hashes = {
            s_id: get_file_sha256(SAMPLES_DIR / s["file_name"])
            for s_id, s in SCENARIOS.items()
        }

        # Regenerate
        generate_all()

        # Second hashes
        second_hashes = {
            s_id: get_file_sha256(SAMPLES_DIR / s["file_name"])
            for s_id, s in SCENARIOS.items()
        }

        assert first_hashes == second_hashes
