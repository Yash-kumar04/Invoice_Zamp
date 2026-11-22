"""Seed master data, ledger entries, and demo history.

Data comes from SPEC.md B6. Every value here matches the spec exactly.
"""

from datetime import timedelta

from app.config import get_as_of_date
from app.db import clear_all_data, post_to_ledger, save_run


# ── Vendors (B6) ───────────────────────────────────────────────────────

VENDORS = [
    {
        "id": "V-001",
        "name": "Northwind Traders Ltd",
        "aliases": ["Northwind Traders", "NWT Ltd"],
        "status": "active",
        "tax_id": "TAX-NW-48291",
        "bank_last4": "4821",
        "terms": "Net 30",
        "callback_phone": "+1-555-0101",
        "domain": "northwind-traders.example",
    },
    {
        "id": "V-002",
        "name": "Apex Industrial Supply Co",
        "aliases": ["Apex Industrial"],
        "status": "active",
        "tax_id": "TAX-AX-77310",
        "bank_last4": "7733",
        "terms": "Net 30",
        "callback_phone": "+1-555-0102",
        "domain": None,
    },
    {
        "id": "V-003",
        "name": "BrightPath IT Services",
        "aliases": ["BrightPath IT"],
        "status": "active",
        "tax_id": "TAX-BP-30415",
        "bank_last4": "1190",
        "terms": "Net 15",
        "callback_phone": "+1-555-0103",
        "domain": "brightpath-it.example",
    },
    {
        "id": "V-004",
        "name": "Meridian Logistics Inc",
        "aliases": ["Meridian Logistics"],
        "status": "active",
        "tax_id": "TAX-ML-55208",
        "bank_last4": "5520",
        "terms": "Net 30",
        "callback_phone": "+1-555-0104",
        "domain": None,
    },
    {
        "id": "V-005",
        "name": "Crestline Office Solutions",
        "aliases": ["Crestline Office"],
        "status": "active",
        "tax_id": "TAX-CR-90817",
        "bank_last4": "6604",
        "terms": "Net 30",
        "callback_phone": "+1-555-0105",
        "domain": None,
    },
    {
        "id": "V-006",
        "name": "Silverline Consulting LLC",
        "aliases": [],
        "status": "blocked",
        "tax_id": "TAX-SL-11902",
        "bank_last4": "3318",
        "terms": "Net 30",
        "callback_phone": "+1-555-0106",
        "domain": None,
    },
]


# ── Purchase Orders (B6) ───────────────────────────────────────────────

PURCHASE_ORDERS = [
    {
        "po_number": "PO-7301", "vendor_id": "V-001",
        "tax_rate": 0.08, "net": 42750.00, "gross": 46170.00,
        "grn_needed": True,
        "lines": [
            {"line": 1, "desc": "Industrial Pressure Sensor S-200",
             "sku": "S-200", "qty": 100, "price": 340.00},
            {"line": 2, "desc": "Mounting Kit MK-10",
             "sku": "MK-10", "qty": 100, "price": 62.50},
            {"line": 3, "desc": "Shielded Cable 5m",
             "sku": None, "qty": 200, "price": 12.50},
        ],
        "received": [100, 100, 200],
    },
    {
        "po_number": "PO-7310", "vendor_id": "V-002",
        "tax_rate": 0.08, "net": 50000.00, "gross": 54000.00,
        "grn_needed": True,
        "lines": [
            {"line": 1, "desc": "Hydraulic Pump HP-45",
             "sku": "HP-45", "qty": 20, "price": 2000.00},
            {"line": 2, "desc": "Seal Replacement Set SR-9",
             "sku": "SR-9", "qty": 50, "price": 200.00},
        ],
        "received": [18, 50],
    },
    {
        "po_number": "PO-7315", "vendor_id": "V-003",
        "tax_rate": 0.10, "net": 12000.00, "gross": 13200.00,
        "grn_needed": False,
        "lines": [
            {"line": 1, "desc": "Cloud migration support, Phase 1 (fixed fee)",
             "sku": None, "qty": 1, "price": 12000.00},
        ],
        "received": [],
    },
    {
        "po_number": "PO-7316", "vendor_id": "V-003",
        "tax_rate": 0.10, "net": 9000.00, "gross": 9900.00,
        "grn_needed": False,
        "lines": [
            {"line": 1, "desc": "Managed support, Q4 2026",
             "sku": None, "qty": 1, "price": 9000.00},
        ],
        "received": [],
    },
    {
        "po_number": "PO-7318", "vendor_id": "V-004",
        "tax_rate": 0.05, "net": 5200.00, "gross": 5460.00,
        "grn_needed": False,
        "lines": [
            {"line": 1, "desc": "Freight services, November lane contract",
             "sku": None, "qty": 1, "price": 5200.00},
        ],
        "received": [],
    },
    {
        "po_number": "PO-7322", "vendor_id": "V-005",
        "tax_rate": 0.08, "net": 2980.00, "gross": 3218.40,
        "grn_needed": True,
        "lines": [
            {"line": 1, "desc": "A4 paper cartons",
             "sku": None, "qty": 40, "price": 22.00},
            {"line": 2, "desc": "Toner cartridge TC-85",
             "sku": "TC-85", "qty": 10, "price": 120.00},
            {"line": 3, "desc": "Desk organiser",
             "sku": None, "qty": 25, "price": 36.00},
        ],
        "received": [40, 10, 25],  # received in full
    },
]


# ── Seed functions ─────────────────────────────────────────────────────

def _seed_vendors(conn):
    for v in VENDORS:
        conn.execute(
            """INSERT INTO vendors
               (id, name, status, tax_id, bank_last4, terms,
                callback_phone, domain)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (v["id"], v["name"], v["status"], v["tax_id"],
             v["bank_last4"], v["terms"], v["callback_phone"],
             v["domain"]),
        )
        for alias in v.get("aliases", []):
            conn.execute(
                "INSERT INTO vendor_aliases (vendor_id, alias) VALUES (?, ?)",
                (v["id"], alias),
            )
    conn.commit()


def _seed_purchase_orders(conn):
    for po in PURCHASE_ORDERS:
        conn.execute(
            """INSERT INTO purchase_orders
               (po_number, vendor_id, tax_rate, net, gross, grn_needed)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (po["po_number"], po["vendor_id"], po["tax_rate"],
             po["net"], po["gross"], 1 if po["grn_needed"] else 0),
        )
        for ln in po["lines"]:
            conn.execute(
                """INSERT INTO po_lines
                   (po_number, line_number, description, sku,
                    quantity, unit_price)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (po["po_number"], ln["line"], ln["desc"],
                 ln["sku"], ln["qty"], ln["price"]),
            )
        for i, qty in enumerate(po.get("received", []), start=1):
            conn.execute(
                """INSERT INTO goods_receipts
                   (po_number, line_number, quantity_received)
                   VALUES (?, ?, ?)""",
                (po["po_number"], i, qty),
            )
    conn.commit()


def _seed_ledger(conn):
    """Post the two invoices already in the ledger per B6."""
    # APX-1042: V-002, PO-7310, L1 10 pumps, net 20,000, gross 21,600
    post_to_ledger(
        conn,
        invoice_number="APX-1042",
        vendor_id="V-002",
        po_number="PO-7310",
        net=20000.00,
        gross=21600.00,
        invoice_date="2026-10-14",
        posted_at="2026-10-14",
        run_id="SEED-01",
        lines=[
            {"po_line_number": 1, "quantity_invoiced": 10,
             "net_amount": 20000.00},
        ],
    )
    # CR-000457: V-005, PO-7322, all lines, net 2980, gross 3218.40
    post_to_ledger(
        conn,
        invoice_number="CR-000457",
        vendor_id="V-005",
        po_number="PO-7322",
        net=2980.00,
        gross=3218.40,
        invoice_date="2026-10-12",
        posted_at="2026-10-12",
        run_id="SEED-02",
        lines=[
            {"po_line_number": 1, "quantity_invoiced": 40,
             "net_amount": 880.00},
            {"po_line_number": 2, "quantity_invoiced": 10,
             "net_amount": 1200.00},
            {"po_line_number": 3, "quantity_invoiced": 25,
             "net_amount": 900.00},
        ],
    )


def _seed_history(conn):
    """Create 14 seeded runs: 8 approved, 3 review, 2 info, 1 rejected.

    SEED-01 and SEED-02 correspond to the two ledger invoices.
    The remaining 12 are purely display data for the dashboard.
    Dates are relative to AS_OF_DATE, spread over 3 weeks.
    """
    today = get_as_of_date()

    # (run_id, invoice_number, vendor_id, vendor_name, po_number,
    #  status, headline, net, gross,
    #  invoice_date, due_date, pay_by, next_action, ms, days_ago)
    runs_data = [
        # ── 8 approved ──────────────────────────────────────────────
        ("SEED-01", "APX-1042", "V-002", "Apex Industrial Supply Co",
         "PO-7310", "APPROVED",
         "Approved. Vendor, PO, goods receipt and amount agree"
         " (variance $0.00). Pay by 2026-11-13.",
         20000.00, 21600.00,
         "2026-10-14", "2026-11-13", "2026-11-13",
         "post_to_ledger", 2340, 21),

        ("SEED-02", "CR-000457", "V-005", "Crestline Office Solutions",
         "PO-7322", "APPROVED",
         "Approved. Vendor, PO, goods receipt and amount agree"
         " (variance $0.00). Pay by 2026-11-11.",
         2980.00, 3218.40,
         "2026-10-12", "2026-11-11", "2026-11-11",
         "post_to_ledger", 1980, 20),

        ("SEED-03", "INV-NW-1001", "V-001", "Northwind Traders Ltd",
         "PO-7301", "APPROVED",
         "Approved. Vendor, PO, goods receipt and amount agree"
         " (variance $0.00). Pay by 2026-12-01.",
         5400.00, 5832.00,
         "2026-11-01", "2026-12-01", "2026-12-01",
         "post_to_ledger", 2100, 19),

        ("SEED-04", "INV-BP-2200", "V-003", "BrightPath IT Services",
         "PO-7315", "APPROVED",
         "Approved. Vendor, PO and amount agree (variance $0.00)."
         " Pay by 2026-11-18.",
         6600.00, 7260.00,
         "2026-11-03", "2026-11-18", "2026-11-18",
         "post_to_ledger", 1850, 17),

        ("SEED-05", "INV-ML-0501", "V-004", "Meridian Logistics Inc",
         "PO-7318", "APPROVED",
         "Approved. Vendor, PO and amount agree (variance $0.00)."
         " Pay by 2026-12-05.",
         2600.00, 2730.00,
         "2026-11-05", "2026-12-05", "2026-12-05",
         "post_to_ledger", 2450, 15),

        ("SEED-06", "INV-NW-1015", "V-001", "Northwind Traders Ltd",
         "PO-7301", "APPROVED",
         "Approved. Vendor, PO, goods receipt and amount agree"
         " (variance $0.00). Pay by 2026-12-07.",
         8100.00, 8748.00,
         "2026-11-07", "2026-12-07", "2026-12-07",
         "post_to_ledger", 1920, 12),

        ("SEED-07", "INV-BP-2250", "V-003", "BrightPath IT Services",
         "PO-7316", "APPROVED",
         "Approved. Vendor, PO and amount agree (variance $0.00)."
         " Pay by 2026-11-25.",
         4500.00, 4950.00,
         "2026-11-10", "2026-11-25", "2026-11-25",
         "post_to_ledger", 2080, 10),

        ("SEED-08", "INV-AX-1050", "V-002", "Apex Industrial Supply Co",
         "PO-7310", "APPROVED",
         "Approved. Vendor, PO, goods receipt and amount agree"
         " (variance $0.00). Pay by 2026-12-12.",
         10800.00, 11664.00,
         "2026-11-12", "2026-12-12", "2026-12-12",
         "post_to_ledger", 2210, 8),

        # ── 3 needs_review ──────────────────────────────────────────
        ("SEED-09", "INV-AX-1030", "V-002", "Apex Industrial Supply Co",
         "PO-7310", "NEEDS_REVIEW",
         "Held for review: amount variance exceeds tolerance.",
         14000.00, 15120.00,
         "2026-11-08", "2026-12-08", None,
         "queue_for_review", 2890, 14),

        ("SEED-10", "INV-ML-0520", "V-004", "Meridian Logistics Inc",
         "PO-7318", "NEEDS_REVIEW",
         "Held for review: probable duplicate requires manual check.",
         2960.00, 3200.00,
         "2026-11-14", "2026-12-14", None,
         "queue_for_review", 3100, 6),

        ("SEED-11", "INV-NW-1030", "V-001", "Northwind Traders Ltd",
         "PO-7301", "NEEDS_REVIEW",
         "Held for review: bank account mismatch.",
         11000.00, 12000.00,
         "2026-11-16", "2026-12-16", None,
         "queue_for_review", 2650, 4),

        # ── 2 needs_info ────────────────────────────────────────────
        ("SEED-12", "INV-CR-0501", "V-005", "Crestline Office Solutions",
         None, "NEEDS_INFO",
         "Waiting on vendor: missing PO reference.",
         1388.89, 1500.00,
         "2026-11-11", None, None,
         "email_vendor", 3450, 9),

        ("SEED-13", None, "V-004", "Meridian Logistics Inc",
         None, "NEEDS_INFO",
         "Waiting on vendor: missing invoice number and PO reference.",
         740.74, 800.00,
         "2026-11-19", None, None,
         "email_vendor", 3800, 2),

        # ── 1 rejected ─────────────────────────────────────────────
        ("SEED-14", "INV-SL-0001", "V-006", "Silverline Consulting LLC",
         None, "REJECTED",
         "Rejected: vendor Silverline Consulting LLC is blocked"
         " (compliance hold).",
         6944.44, 7500.00,
         "2026-11-18", None, None,
         "none", 1200, 3),
    ]

    # Representative checks per status for dashboard display
    _CHECKS = {
        "APPROVED": [
            {"check_id": "EXT-01", "check_name": "Critical fields present",
             "stage": "verify", "result": "pass"},
            {"check_id": "VEN-01", "check_name": "Vendor match",
             "stage": "vendor", "result": "pass"},
            {"check_id": "VEN-02", "check_name": "Vendor active",
             "stage": "vendor", "result": "pass"},
            {"check_id": "DUP-01", "check_name": "Duplicate check",
             "stage": "duplicate", "result": "pass"},
            {"check_id": "PO-01", "check_name": "PO found",
             "stage": "po_match", "result": "pass"},
            {"check_id": "AMT-01", "check_name": "Amount variance",
             "stage": "amounts", "result": "pass"},
        ],
        "NEEDS_REVIEW": [
            {"check_id": "EXT-01", "check_name": "Critical fields present",
             "stage": "verify", "result": "pass"},
            {"check_id": "VEN-01", "check_name": "Vendor match",
             "stage": "vendor", "result": "pass"},
            {"check_id": "AMT-01", "check_name": "Amount variance",
             "stage": "amounts", "result": "fail",
             "message": "Variance exceeds tolerance"},
        ],
        "NEEDS_INFO": [
            {"check_id": "EXT-01", "check_name": "Critical fields present",
             "stage": "verify", "result": "fail",
             "message": "Missing required fields"},
        ],
        "REJECTED": [
            {"check_id": "EXT-01", "check_name": "Critical fields present",
             "stage": "verify", "result": "pass"},
            {"check_id": "VEN-01", "check_name": "Vendor match",
             "stage": "vendor", "result": "pass"},
            {"check_id": "VEN-02", "check_name": "Vendor active",
             "stage": "vendor", "result": "fail",
             "message": "Vendor blocked: compliance hold"},
        ],
    }

    for row in runs_data:
        (run_id, inv_num, vid, vname, po, status, headline,
         net, gross, inv_date, due, pay_by, action, ms, days) = row
        created = (today - timedelta(days=days)).isoformat()

        run_dict = {
            "run_id": run_id,
            "invoice_number": inv_num,
            "vendor_id": vid,
            "vendor_name": vname,
            "po_number": po,
            "status": status,
            "headline": headline,
            "net": net,
            "gross": gross,
            "currency": "USD",
            "invoice_date": inv_date,
            "due_date": due,
            "pay_by": pay_by,
            "next_action": action,
            "processing_time_ms": ms,
            "created_at": created,
            "seeded": 1,
        }
        save_run(conn, run_dict, _CHECKS.get(status, []))


# ── Public API ─────────────────────────────────────────────────────────

def seed_all(conn):
    """Seed all master data, ledger entries, and demo history."""
    _seed_vendors(conn)
    _seed_purchase_orders(conn)
    _seed_ledger(conn)
    _seed_history(conn)


def reset_demo_data(conn):
    """Clear everything and re-seed from scratch."""
    clear_all_data(conn)
    seed_all(conn)
