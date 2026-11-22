"""Tests for seed data — verify all B6 values exactly."""

import pytest

from app.db import (
    get_all_vendors, get_vendor, get_vendor_aliases,
    get_all_purchase_orders, get_purchase_order, get_po_lines,
    get_goods_receipt, get_po_remaining_net, get_po_line_remaining_qty,
    get_ledger_entries, get_ledger_lines,
    get_runs, count_runs_by_status, get_run, get_run_checks,
    is_db_seeded,
)
from app.seed import reset_demo_data, VENDORS, PURCHASE_ORDERS
from app.config import load_rules, get_as_of_date


# ── Config ──────────────────────────────────────────────────────────────

class TestConfig:
    def test_rules_load(self):
        rules = load_rules()
        assert rules["currency"] == "USD"
        assert rules["tolerance_pct"] == 2.0
        assert rules["tolerance_abs"] == 100.00
        assert rules["auto_approve_limit"] == 75000
        assert rules["stale_invoice_days"] == 180
        assert rules["vendor_fuzzy_accept"] == 92
        assert rules["manual_minutes_per_invoice"] == 8
        assert rules["allow_implied_po_autoapprove"] is False

    def test_as_of_date(self):
        from datetime import date
        assert get_as_of_date() == date(2026, 11, 22)


# ── Vendors ─────────────────────────────────────────────────────────────

class TestVendors:
    def test_vendor_count(self, seeded_db):
        assert len(get_all_vendors(seeded_db)) == 6

    @pytest.mark.parametrize(
        "vid,name,status,tax_id,bank,terms,phone,domain",
        [
            ("V-001", "Northwind Traders Ltd", "active",
             "TAX-NW-48291", "4821", "Net 30",
             "+1-555-0101", "northwind-traders.example"),
            ("V-002", "Apex Industrial Supply Co", "active",
             "TAX-AX-77310", "7733", "Net 30",
             "+1-555-0102", None),
            ("V-003", "BrightPath IT Services", "active",
             "TAX-BP-30415", "1190", "Net 15",
             "+1-555-0103", "brightpath-it.example"),
            ("V-004", "Meridian Logistics Inc", "active",
             "TAX-ML-55208", "5520", "Net 30",
             "+1-555-0104", None),
            ("V-005", "Crestline Office Solutions", "active",
             "TAX-CR-90817", "6604", "Net 30",
             "+1-555-0105", None),
            ("V-006", "Silverline Consulting LLC", "blocked",
             "TAX-SL-11902", "3318", "Net 30",
             "+1-555-0106", None),
        ],
    )
    def test_vendor_fields(self, seeded_db, vid, name, status,
                           tax_id, bank, terms, phone, domain):
        v = get_vendor(seeded_db, vid)
        assert v is not None
        assert v["name"] == name
        assert v["status"] == status
        assert v["tax_id"] == tax_id
        assert v["bank_last4"] == bank
        assert v["terms"] == terms
        assert v["callback_phone"] == phone
        assert v["domain"] == domain

    def test_v001_aliases(self, seeded_db):
        aliases = get_vendor_aliases(seeded_db, "V-001")
        assert set(aliases) == {"Northwind Traders", "NWT Ltd"}

    def test_v002_aliases(self, seeded_db):
        assert get_vendor_aliases(seeded_db, "V-002") == ["Apex Industrial"]

    def test_v003_aliases(self, seeded_db):
        assert get_vendor_aliases(seeded_db, "V-003") == ["BrightPath IT"]

    def test_v006_no_aliases(self, seeded_db):
        assert get_vendor_aliases(seeded_db, "V-006") == []

    def test_seed_constants_match_db(self, seeded_db):
        """Verify the VENDORS constant agrees with the DB."""
        for v in VENDORS:
            row = get_vendor(seeded_db, v["id"])
            assert row["name"] == v["name"]
            assert row["tax_id"] == v["tax_id"]


# ── Purchase Orders ─────────────────────────────────────────────────────

class TestPurchaseOrders:
    def test_po_count(self, seeded_db):
        assert len(get_all_purchase_orders(seeded_db)) == 6

    @pytest.mark.parametrize(
        "po_num,vendor,tax,net,gross,grn",
        [
            ("PO-7301", "V-001", 0.08, 42750.00, 46170.00, 1),
            ("PO-7310", "V-002", 0.08, 50000.00, 54000.00, 1),
            ("PO-7315", "V-003", 0.10, 12000.00, 13200.00, 0),
            ("PO-7316", "V-003", 0.10,  9000.00,  9900.00, 0),
            ("PO-7318", "V-004", 0.05,  5200.00,  5460.00, 0),
            ("PO-7322", "V-005", 0.08,  2980.00,  3218.40, 1),
        ],
    )
    def test_po_fields(self, seeded_db, po_num, vendor, tax,
                       net, gross, grn):
        po = get_purchase_order(seeded_db, po_num)
        assert po is not None
        assert po["vendor_id"] == vendor
        assert po["tax_rate"] == pytest.approx(tax)
        assert po["net"] == pytest.approx(net)
        assert po["gross"] == pytest.approx(gross)
        assert po["grn_needed"] == grn

    def test_po7301_lines(self, seeded_db):
        lines = get_po_lines(seeded_db, "PO-7301")
        assert len(lines) == 3
        # L1: Industrial Pressure Sensor S-200, 100 × 340.00
        assert lines[0]["sku"] == "S-200"
        assert lines[0]["quantity"] == 100
        assert lines[0]["unit_price"] == pytest.approx(340.00)
        # L2: Mounting Kit MK-10, 100 × 62.50
        assert lines[1]["sku"] == "MK-10"
        assert lines[1]["quantity"] == 100
        assert lines[1]["unit_price"] == pytest.approx(62.50)
        # L3: Shielded Cable 5m, 200 × 12.50
        assert lines[2]["sku"] is None
        assert lines[2]["quantity"] == 200
        assert lines[2]["unit_price"] == pytest.approx(12.50)

    def test_po7310_lines(self, seeded_db):
        lines = get_po_lines(seeded_db, "PO-7310")
        assert len(lines) == 2
        assert lines[0]["sku"] == "HP-45"
        assert lines[0]["quantity"] == 20
        assert lines[0]["unit_price"] == pytest.approx(2000.00)
        assert lines[1]["sku"] == "SR-9"
        assert lines[1]["quantity"] == 50
        assert lines[1]["unit_price"] == pytest.approx(200.00)

    def test_po7322_lines(self, seeded_db):
        lines = get_po_lines(seeded_db, "PO-7322")
        assert len(lines) == 3
        assert lines[0]["quantity"] == 40
        assert lines[0]["unit_price"] == pytest.approx(22.00)
        assert lines[1]["sku"] == "TC-85"
        assert lines[1]["quantity"] == 10
        assert lines[1]["unit_price"] == pytest.approx(120.00)
        assert lines[2]["quantity"] == 25
        assert lines[2]["unit_price"] == pytest.approx(36.00)

    def test_po7315_service_line(self, seeded_db):
        lines = get_po_lines(seeded_db, "PO-7315")
        assert len(lines) == 1
        assert lines[0]["sku"] is None
        assert lines[0]["quantity"] == 1
        assert lines[0]["unit_price"] == pytest.approx(12000.00)

    def test_po_net_equals_line_totals(self, seeded_db):
        """Every PO's net must equal the sum of qty × price."""
        for po_data in PURCHASE_ORDERS:
            po = get_purchase_order(seeded_db, po_data["po_number"])
            lines = get_po_lines(seeded_db, po_data["po_number"])
            line_total = sum(l["quantity"] * l["unit_price"] for l in lines)
            assert po["net"] == pytest.approx(line_total), (
                f"{po_data['po_number']} net mismatch"
            )


# ── Goods Receipts ──────────────────────────────────────────────────────

class TestGoodsReceipts:
    def test_po7301_grn(self, seeded_db):
        for ln, qty in [(1, 100), (2, 100), (3, 200)]:
            gr = get_goods_receipt(seeded_db, "PO-7301", ln)
            assert gr is not None
            assert gr["quantity_received"] == qty

    def test_po7310_grn(self, seeded_db):
        gr1 = get_goods_receipt(seeded_db, "PO-7310", 1)
        assert gr1["quantity_received"] == 18
        gr2 = get_goods_receipt(seeded_db, "PO-7310", 2)
        assert gr2["quantity_received"] == 50

    def test_po7322_grn_full(self, seeded_db):
        for ln, qty in [(1, 40), (2, 10), (3, 25)]:
            gr = get_goods_receipt(seeded_db, "PO-7322", ln)
            assert gr["quantity_received"] == qty

    def test_service_po_no_grn(self, seeded_db):
        """Service POs (PO-7315, PO-7316, PO-7318) have no GRN."""
        for po in ["PO-7315", "PO-7316", "PO-7318"]:
            gr = get_goods_receipt(seeded_db, po, 1)
            assert gr is None


# ── Ledger ──────────────────────────────────────────────────────────────

class TestLedger:
    def test_ledger_count(self, seeded_db):
        assert len(get_ledger_entries(seeded_db)) == 2

    def test_apx1042(self, seeded_db):
        entries = get_ledger_entries(seeded_db, invoice_number="APX-1042")
        assert len(entries) == 1
        e = entries[0]
        assert e["vendor_id"] == "V-002"
        assert e["po_number"] == "PO-7310"
        assert e["net"] == pytest.approx(20000.00)
        assert e["gross"] == pytest.approx(21600.00)
        assert e["invoice_date"] == "2026-10-14"
        assert e["run_id"] == "SEED-01"
        # Line detail: L1, 10 pumps
        lines = get_ledger_lines(seeded_db, e["id"])
        assert len(lines) == 1
        assert lines[0]["po_line_number"] == 1
        assert lines[0]["quantity_invoiced"] == 10
        assert lines[0]["net_amount"] == pytest.approx(20000.00)

    def test_cr000457(self, seeded_db):
        entries = get_ledger_entries(seeded_db, invoice_number="CR-000457")
        assert len(entries) == 1
        e = entries[0]
        assert e["vendor_id"] == "V-005"
        assert e["po_number"] == "PO-7322"
        assert e["net"] == pytest.approx(2980.00)
        assert e["gross"] == pytest.approx(3218.40)
        assert e["invoice_date"] == "2026-10-12"
        assert e["run_id"] == "SEED-02"
        # All 3 lines, net sums to 2980
        lines = get_ledger_lines(seeded_db, e["id"])
        assert len(lines) == 3
        assert sum(l["net_amount"] for l in lines) == pytest.approx(2980.00)
        # Individual lines
        assert lines[0]["quantity_invoiced"] == 40   # A4 paper
        assert lines[1]["quantity_invoiced"] == 10   # Toner
        assert lines[2]["quantity_invoiced"] == 25   # Desk organiser


# ── PO Remaining Balances ───────────────────────────────────────────────

class TestPORemainingBalances:
    """Verify computed remaining balances after ledger postings."""

    def test_po7310_remaining_net(self, seeded_db):
        # 50,000 - 20,000 (APX-1042) = 30,000
        assert get_po_remaining_net(seeded_db, "PO-7310") == \
            pytest.approx(30000.00)

    def test_po7310_l1_remaining_qty(self, seeded_db):
        # 18 received - 10 billed = 8
        assert get_po_line_remaining_qty(seeded_db, "PO-7310", 1) == \
            pytest.approx(8)

    def test_po7310_l2_remaining_qty(self, seeded_db):
        # 50 received - 0 billed = 50
        assert get_po_line_remaining_qty(seeded_db, "PO-7310", 2) == \
            pytest.approx(50)

    def test_po7322_fully_billed_net(self, seeded_db):
        assert get_po_remaining_net(seeded_db, "PO-7322") == \
            pytest.approx(0.00)

    def test_po7322_lines_fully_billed(self, seeded_db):
        for ln in [1, 2, 3]:
            assert get_po_line_remaining_qty(seeded_db, "PO-7322", ln) == \
                pytest.approx(0)

    def test_po7301_untouched(self, seeded_db):
        # No ledger entries for PO-7301
        assert get_po_remaining_net(seeded_db, "PO-7301") == \
            pytest.approx(42750.00)

    def test_po7315_untouched(self, seeded_db):
        assert get_po_remaining_net(seeded_db, "PO-7315") == \
            pytest.approx(12000.00)

    def test_po7316_untouched(self, seeded_db):
        assert get_po_remaining_net(seeded_db, "PO-7316") == \
            pytest.approx(9000.00)

    def test_po7318_untouched(self, seeded_db):
        assert get_po_remaining_net(seeded_db, "PO-7318") == \
            pytest.approx(5200.00)


# ── Seeded Runs ─────────────────────────────────────────────────────────

class TestSeededRuns:
    def test_total_count(self, seeded_db):
        runs = get_runs(seeded_db, seeded=True)
        assert len(runs) == 14

    def test_status_distribution(self, seeded_db):
        counts = count_runs_by_status(seeded_db, seeded=True)
        assert counts["APPROVED"] == 8
        assert counts["NEEDS_REVIEW"] == 3
        assert counts["NEEDS_INFO"] == 2
        assert counts["REJECTED"] == 1

    def test_seed01_is_apx1042(self, seeded_db):
        run = get_run(seeded_db, "SEED-01")
        assert run["invoice_number"] == "APX-1042"
        assert run["vendor_id"] == "V-002"
        assert run["status"] == "APPROVED"
        assert run["gross"] == pytest.approx(21600.00)
        assert run["seeded"] == 1

    def test_seed02_is_cr000457(self, seeded_db):
        run = get_run(seeded_db, "SEED-02")
        assert run["invoice_number"] == "CR-000457"
        assert run["vendor_id"] == "V-005"
        assert run["status"] == "APPROVED"
        assert run["gross"] == pytest.approx(3218.40)

    def test_all_runs_are_seeded(self, seeded_db):
        runs = get_runs(seeded_db, seeded=True)
        assert all(r["seeded"] == 1 for r in runs)

    def test_all_runs_have_checks(self, seeded_db):
        for i in range(1, 15):
            rid = f"SEED-{i:02d}"
            checks = get_run_checks(seeded_db, rid)
            assert len(checks) > 0, f"{rid} has no checks"

    def test_all_runs_have_created_at(self, seeded_db):
        runs = get_runs(seeded_db, seeded=True)
        for r in runs:
            assert r["created_at"] is not None


# ── Reset ───────────────────────────────────────────────────────────────

class TestResetDemoData:
    def test_reset_clears_and_reseeds(self, seeded_db):
        assert is_db_seeded(seeded_db)
        reset_demo_data(seeded_db)
        assert is_db_seeded(seeded_db)
        assert len(get_all_vendors(seeded_db)) == 6
        assert len(get_runs(seeded_db, seeded=True)) == 14

    def test_reset_clears_non_seeded_runs(self, seeded_db):
        from app.db import save_run
        save_run(seeded_db, {
            "run_id": "LIVE-001",
            "invoice_number": "TEST-001",
            "vendor_id": "V-001",
            "vendor_name": "Test",
            "po_number": None,
            "status": "APPROVED",
            "created_at": "2026-11-22T00:00:00",
        })
        assert len(get_runs(seeded_db)) == 15
        reset_demo_data(seeded_db)
        assert len(get_runs(seeded_db)) == 14
        assert all(r["seeded"] == 1 for r in get_runs(seeded_db))

    def test_reset_restores_po_balances(self, seeded_db):
        """After reset, PO balances are back to seeded state."""
        reset_demo_data(seeded_db)
        assert get_po_remaining_net(seeded_db, "PO-7310") == \
            pytest.approx(30000.00)
        assert get_po_remaining_net(seeded_db, "PO-7322") == \
            pytest.approx(0.00)
