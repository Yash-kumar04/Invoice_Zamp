"""Scenarios definition and deterministic sample invoice generator.

ONE source of truth for the 7 demo scenarios (S0, S1, E1a, E1b, E2, E3, E4).
Generates:
1. samples/<id>_*.pdf (4 visibly different layouts, deterministic reportlab output)
2. E2 and E3 as IMAGE-ONLY PDFs with ZERO text layer (rasterised via PyMuPDF + Pillow)
3. fixtures/extractions/<id>.json in exact B7 schema
4. samples/manifest.json mapping SHA-256 hashes to scenario IDs
"""

import hashlib
import io
import json
import os
from pathlib import Path
import random

# ReportLab imports
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
import reportlab.rl_config

reportlab.rl_config.invariant = 1

# PIL and PyMuPDF imports
from PIL import Image, ImageFilter, ImageDraw
import numpy as np
import pymupdf as fitz

BASE_DIR = Path(__file__).resolve().parent.parent
SAMPLES_DIR = BASE_DIR / "samples"
FIXTURES_DIR = BASE_DIR / "fixtures" / "extractions"


# ── Scenario Master Data (Source of Truth) ──────────────────────────────

SCENARIOS = {
    "S0": {
        "id": "S0",
        "file_name": "S0_Northwind_INV-20831.pdf",
        "vendor_id": "V-001",
        "vendor_name": "Northwind Traders Ltd",
        "vendor_tax_id": "TAX-NW-48291",
        "vendor_email": "billing@northwind-traders.example",
        "vendor_phone": "+1-555-0101",
        "vendor_address": "100 Industrial Parkway, Suite 400, Chicago, IL 60601",
        "bank_last4": "4821",
        "invoice_number": "INV-20831",
        "invoice_date": "2026-11-04",
        "invoice_date_raw": "4 Nov 2026",
        "due_date": "2026-12-04",
        "due_date_raw": "4 Dec 2026",
        "po_reference": "PO-7301",
        "other_references": [],
        "currency": "USD",
        "payment_terms": "Net 30",
        "tax_rate": 0.08,
        "tax_mode": "separate",
        "subtotal": 42750.00,
        "tax_amount": 3420.00,
        "discount": 0.0,
        "total_amount": 46170.00,
        "line_items": [
            {
                "line": 1,
                "sku": "S-200",
                "description": "Industrial Pressure Sensor S-200",
                "quantity": 100,
                "unit_price": 340.00,
                "line_total": 34000.00,
            },
            {
                "line": 2,
                "sku": "MK-10",
                "description": "Mounting Kit MK-10",
                "quantity": 100,
                "unit_price": 62.50,
                "line_total": 6250.00,
            },
            {
                "line": 3,
                "sku": None,
                "description": "Shielded Cable 5m",
                "quantity": 200,
                "unit_price": 12.50,
                "line_total": 2500.00,
            },
        ],
        "page_notes": None,
        "document_quality": "clean",
        "is_image_only": False,
        "missing_fields": [],
        "layout": "classic_navy",
    },
    "S1": {
        "id": "S1",
        "file_name": "S1_BrightPath_INV-BP-2291.pdf",
        "vendor_id": "V-003",
        "vendor_name": "BrightPath IT Services",
        "vendor_tax_id": "TAX-BP-30415",
        "vendor_email": "accounts@brightpath-it.example",
        "vendor_phone": "+1-555-0103",
        "vendor_address": "450 Technology Square, Cambridge, MA 02139",
        "bank_last4": "1190",
        "invoice_number": "INV-BP-2291",
        "invoice_date": "2026-11-10",
        "invoice_date_raw": "10 Nov 2026",
        "due_date": "2026-11-25",
        "due_date_raw": "25 Nov 2026",
        "po_reference": "PO-7315",
        "other_references": [],
        "currency": "USD",
        "payment_terms": "Net 15",
        "tax_rate": 0.10,
        "tax_mode": "included",
        "subtotal": 12027.27,
        "tax_amount": 1202.73,
        "discount": 0.0,
        "total_amount": 13230.00,
        "line_items": [
            {
                "line": 1,
                "sku": None,
                "description": "Cloud migration support and consulting services (Phase 1 bundled)",
                "quantity": 1,
                "unit_price": 13230.00,
                "line_total": 13230.00,
            }
        ],
        "page_notes": "Total due $13,230.00 includes 10% sales tax.",
        "document_quality": "clean",
        "is_image_only": False,
        "missing_fields": [],
        "layout": "modern_teal",
    },
    "E1a": {
        "id": "E1a",
        "file_name": "E1a_Apex_APX-1055.pdf",
        "vendor_id": "V-002",
        "vendor_name": "Apex Industrial Supply Co",
        "vendor_tax_id": "TAX-AX-77310",
        "vendor_email": "billing@apexindustrial.example",
        "vendor_phone": "+1-555-0102",
        "vendor_address": "820 Heavy Machinery Way, Cleveland, OH 44114",
        "bank_last4": "7733",
        "invoice_number": "APX-1055",
        "invoice_date": "2026-11-12",
        "invoice_date_raw": "12 Nov 2026",
        "due_date": "2026-12-12",
        "due_date_raw": "12 Dec 2026",
        "po_reference": "PO-7310",
        "other_references": [],
        "currency": "USD",
        "payment_terms": "Net 30",
        "tax_rate": 0.08,
        "tax_mode": "separate",
        "subtotal": 26000.00,
        "tax_amount": 2080.00,
        "discount": 0.0,
        "total_amount": 28080.00,
        "line_items": [
            {
                "line": 1,
                "sku": "HP-45",
                "description": "Hydraulic Pump HP-45",
                "quantity": 8,
                "unit_price": 2000.00,
                "line_total": 16000.00,
            },
            {
                "line": 2,
                "sku": "SR-9",
                "description": "Seal Replacement Set SR-9",
                "quantity": 50,
                "unit_price": 200.00,
                "line_total": 10000.00,
            },
        ],
        "page_notes": "Partial shipment 1 against PO-7310.",
        "document_quality": "clean",
        "is_image_only": False,
        "missing_fields": [],
        "layout": "industrial_bold",
    },
    "E1b": {
        "id": "E1b",
        "file_name": "E1b_Apex_APX-1061.pdf",
        "vendor_id": "V-002",
        "vendor_name": "Apex Industrial Supply Co",
        "vendor_tax_id": "TAX-AX-77310",
        "vendor_email": "billing@apexindustrial.example",
        "vendor_phone": "+1-555-0102",
        "vendor_address": "820 Heavy Machinery Way, Cleveland, OH 44114",
        "bank_last4": "7733",
        "invoice_number": "APX-1061",
        "invoice_date": "2026-11-18",
        "invoice_date_raw": "18 Nov 2026",
        "due_date": "2026-12-18",
        "due_date_raw": "18 Dec 2026",
        "po_reference": "PO-7310",
        "other_references": [],
        "currency": "USD",
        "payment_terms": "Net 30",
        "tax_rate": 0.08,
        "tax_mode": "separate",
        "subtotal": 8000.00,
        "tax_amount": 640.00,
        "discount": 0.0,
        "total_amount": 8640.00,
        "line_items": [
            {
                "line": 1,
                "sku": "HP-45",
                "description": "Hydraulic Pump HP-45",
                "quantity": 4,
                "unit_price": 2000.00,
                "line_total": 8000.00,
            }
        ],
        "page_notes": "Partial shipment 2 against PO-7310.",
        "document_quality": "clean",
        "is_image_only": False,
        "missing_fields": [],
        "layout": "industrial_bold",
    },
    "E2": {
        "id": "E2",
        "file_name": "E2_Crestline_CR457.pdf",
        "vendor_id": "V-005",
        "vendor_name": "Crestline Office Solutions",
        "vendor_tax_id": "TAX-CR-90817",
        "vendor_email": "ar@crestlineoffice.example",
        "vendor_phone": "+1-555-0105",
        "vendor_address": "312 Commercial Boulevard, Denver, CO 80202",
        "bank_last4": "6604",
        "invoice_number": "CR 457",  # Notice: printed as "CR 457"
        "invoice_date": "2026-10-12",
        "invoice_date_raw": "12 Oct 2026",
        "due_date": "2026-11-11",
        "due_date_raw": "11 Nov 2026",
        "po_reference": "PO7322",  # Notice: printed without dash
        "other_references": [],
        "currency": "USD",
        "payment_terms": "Net 30",
        "tax_rate": 0.08,
        "tax_mode": "separate",
        "subtotal": 2980.00,
        "tax_amount": 238.40,
        "discount": 0.0,
        "total_amount": 3218.40,
        "line_items": [
            {
                "line": 1,
                "sku": None,
                "description": "A4 paper cartons",
                "quantity": 40,
                "unit_price": 22.00,
                "line_total": 880.00,
            },
            {
                "line": 2,
                "sku": "TC-85",
                "description": "Toner cartridge TC-85",
                "quantity": 10,
                "unit_price": 120.00,
                "line_total": 1200.00,
            },
            {
                "line": 3,
                "sku": None,
                "description": "Desk organiser",
                "quantity": 25,
                "unit_price": 36.00,
                "line_total": 900.00,
            },
        ],
        "page_notes": "Scanned copy / customer duplicate.",
        "document_quality": "scanned_ok",
        "is_image_only": True,
        "missing_fields": [],
        "layout": "office_classic",
    },
    "E3": {
        "id": "E3",
        "file_name": "E3_Meridian_Scan.pdf",
        "vendor_id": "V-004",
        "vendor_name": "Meridian Logistics Inc",
        "vendor_tax_id": "TAX-ML-55208",
        "vendor_email": "dispatch@meridian-logistics.example",
        "vendor_phone": "+1-555-0104",
        "vendor_address": "77 Logistics Way, Memphis, TN 38118",
        "bank_last4": None,  # Not visible
        "invoice_number": None,  # Not printed
        "invoice_date": None,  # Not printed
        "invoice_date_raw": None,
        "due_date": None,
        "due_date_raw": None,
        "po_reference": None,  # Not printed
        "other_references": ["Quote Q-8890"],
        "currency": "USD",
        "payment_terms": "Net 30",
        "tax_rate": 0.05,
        "tax_mode": "separate",
        "subtotal": 5200.00,
        "tax_amount": 260.00,
        "discount": 0.0,
        "total_amount": 5460.00,
        "line_items": [
            {
                "line": 1,
                "sku": None,
                "description": "Freight services, November lane contract",
                "quantity": 1,
                "unit_price": 5200.00,
                "line_total": 5200.00,
            }
        ],
        "page_notes": "Re: Quote Q-8890. Delivery completed.",
        "document_quality": "scanned_poor",
        "is_image_only": True,
        "missing_fields": ["invoice_number", "invoice_date"],
        "layout": "shipping_waybill",
    },
    "E4": {
        "id": "E4",
        "file_name": "E4_BrightPath_INV-BP-2304.pdf",
        "vendor_id": "V-003",
        "vendor_name": "BrightPath IT Services",
        "vendor_tax_id": "TAX-BP-30415",
        "vendor_email": "ap@brightpath-billing.example",  # Changed domain!
        "vendor_phone": "+1-555-0103",
        "vendor_address": "450 Technology Square, Cambridge, MA 02139",
        "bank_last4": "9027",  # Changed bank details!
        "invoice_number": "INV-BP-2304",
        "invoice_date": "2026-11-15",
        "invoice_date_raw": "15 Nov 2026",
        "due_date": "2026-11-30",
        "due_date_raw": "30 Nov 2026",
        "po_reference": "PO-7316",
        "other_references": [],
        "currency": "USD",
        "payment_terms": "Net 15",
        "tax_rate": 0.10,
        "tax_mode": "separate",
        "subtotal": 9000.00,
        "tax_amount": 900.00,
        "discount": 0.0,
        "total_amount": 9900.00,
        "line_items": [
            {
                "line": 1,
                "sku": None,
                "description": "Managed support, Q4 2026",
                "quantity": 1,
                "unit_price": 9000.00,
                "line_total": 9000.00,
            }
        ],
        "page_notes": (
            "IMPORTANT NOTICE: Our banking details have changed effective November 2026. "
            "Please update your remittance records: Account ending in 9027. "
            "Remit email: ap@brightpath-billing.example."
        ),
        "document_quality": "clean",
        "is_image_only": False,
        "missing_fields": [],
        "layout": "modern_teal_alert",
    },
}


# ── ReportLab PDF Generators (4 Visibly Different Layouts) ──────────────

def _build_pdf_flowables(s: dict) -> list:
    """Build flowables according to the vendor layout theme."""
    styles = getSampleStyleSheet()
    flowables = []

    layout = s["layout"]

    if layout == "classic_navy":
        # 1. Classic Corporate Navy (Northwind Traders)
        h_style = ParagraphStyle("H1", fontName="Helvetica-Bold", fontSize=22, textColor=colors.HexColor("#1e3a8a"), leading=26)
        sub_style = ParagraphStyle("Sub", fontName="Helvetica", fontSize=9, textColor=colors.HexColor("#4b5563"), leading=13)
        right_title = ParagraphStyle("RT", fontName="Helvetica-Bold", fontSize=18, textColor=colors.HexColor("#1e3a8a"), alignment=2)
        right_meta = ParagraphStyle("RM", fontName="Helvetica", fontSize=9, textColor=colors.HexColor("#1f2937"), alignment=2, leading=14)

        header_table = Table(
            [
                [
                    Paragraph(f"<b>{s['vendor_name']}</b><br/>{s['vendor_address']}<br/>Tax ID: {s['vendor_tax_id']}<br/>Phone: {s['vendor_phone']}", sub_style),
                    Paragraph(f"<b>INVOICE</b><br/>Invoice #: <b>{s['invoice_number']}</b><br/>Date: {s['invoice_date_raw']}<br/>Due Date: {s['due_date_raw']}<br/>PO Ref: <b>{s['po_reference']}</b>", right_meta),
                ]
            ],
            colWidths=[300, 240],
        )
        header_table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ]))
        flowables.append(header_table)
        flowables.append(Spacer(1, 15))

        # Items Table
        data = [["Line", "SKU", "Description", "Qty", "Unit Price ($)", "Total ($)"]]
        for item in s["line_items"]:
            data.append([
                str(item["line"]),
                item["sku"] or "-",
                item["description"],
                str(item["quantity"]),
                f"{item['unit_price']:,.2f}",
                f"{item['line_total']:,.2f}",
            ])

        t = Table(data, colWidths=[35, 70, 235, 40, 80, 80])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e3a8a")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("ALIGN", (3, 0), (-1, -1), "RIGHT"),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
        ]))
        flowables.append(t)
        flowables.append(Spacer(1, 12))

        # Totals
        totals_data = [
            ["Subtotal:", f"${s['subtotal']:,.2f}"],
            [f"Tax ({s['tax_rate']*100:.0f}%):", f"${s['tax_amount']:,.2f}"],
            ["Total Due (USD):", f"${s['total_amount']:,.2f}"],
        ]
        tot_table = Table(totals_data, colWidths=[420, 120])
        tot_table.setStyle(TableStyle([
            ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
            ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("FONTNAME", (0, 2), (-1, 2), "Helvetica-Bold"),
            ("FONTSIZE", (0, 2), (-1, 2), 11),
            ("TEXTCOLOR", (0, 2), (-1, 2), colors.HexColor("#1e3a8a")),
            ("LINEABOVE", (0, 2), (-1, 2), 1, colors.HexColor("#1e3a8a")),
        ]))
        flowables.append(tot_table)
        flowables.append(Spacer(1, 25))

        # Remittance Footer
        remit_p = Paragraph(
            f"<b>Remittance Information:</b> Bank Account ending in <b>{s['bank_last4']}</b> | Terms: {s['payment_terms']}<br/>"
            f"Email queries to: {s['vendor_email']}",
            sub_style,
        )
        flowables.append(remit_p)

    elif layout in ("modern_teal", "modern_teal_alert"):
        # 2. Modern Tech SaaS (BrightPath IT Services)
        teal = colors.HexColor("#0f766e")
        h_style = ParagraphStyle("H1T", fontName="Helvetica-Bold", fontSize=24, textColor=teal, leading=28)
        meta_label = ParagraphStyle("ML", fontName="Helvetica-Bold", fontSize=8, textColor=colors.HexColor("#64748b"))
        meta_val = ParagraphStyle("MV", fontName="Helvetica", fontSize=9, textColor=colors.HexColor("#0f172a"))

        flowables.append(Paragraph(f"<b>{s['vendor_name']}</b>", h_style))
        flowables.append(Paragraph(f"{s['vendor_address']} | Tax ID: {s['vendor_tax_id']}", meta_val))
        flowables.append(Spacer(1, 15))

        # Alert Banner for E4
        if layout == "modern_teal_alert" and s.get("page_notes"):
            alert_style = ParagraphStyle("Alert", fontName="Helvetica-Bold", fontSize=9, textColor=colors.HexColor("#991b1b"), leading=13)
            alert_box = Table(
                [[Paragraph(f"<b>IMPORTANT: BANK DETAILS CHANGE NOTICE</b><br/>{s['page_notes']}", alert_style)]],
                colWidths=[540],
            )
            alert_box.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#fee2e2")),
                ("BOX", (0, 0), (-1, -1), 1.5, colors.HexColor("#ef4444")),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
            ]))
            flowables.append(alert_box)
            flowables.append(Spacer(1, 15))

        meta_grid = [
            ["INVOICE NUMBER", "DATE", "DUE DATE", "PO NUMBER"],
            [s["invoice_number"], s["invoice_date_raw"], s["due_date_raw"], s["po_reference"]],
        ]
        mg = Table(meta_grid, colWidths=[135, 135, 135, 135])
        mg.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 8),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#64748b")),
            ("FONTNAME", (0, 1), (-1, 1), "Helvetica-Bold"),
            ("FONTSIZE", (0, 1), (-1, 1), 10),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        flowables.append(mg)
        flowables.append(Spacer(1, 15))

        # Line items
        data = [["Service Description", "Qty", "Unit Rate", "Amount ($)"]]
        for item in s["line_items"]:
            data.append([
                item["description"],
                str(item["quantity"]),
                f"${item['unit_price']:,.2f}",
                f"${item['line_total']:,.2f}",
            ])

        t = Table(data, colWidths=[320, 50, 80, 90])
        t.setStyle(TableStyle([
            ("LINEBELOW", (0, 0), (-1, 0), 2, teal),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ("LINEBELOW", (0, 1), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
        ]))
        flowables.append(t)
        flowables.append(Spacer(1, 10))

        # Total Banner
        if s["tax_mode"] == "included":
            tot_label = f"Total Due (includes {s['tax_rate']*100:.0f}% tax):"
        else:
            tot_label = f"Subtotal: ${s['subtotal']:,.2f}  |  Tax ({s['tax_rate']*100:.0f}%): ${s['tax_amount']:,.2f}  |  Total:"

        tot_p = Paragraph(f"<b>{tot_label}</b> <font size=14><b>${s['total_amount']:,.2f} USD</b></font>", ParagraphStyle("TotT", fontName="Helvetica", fontSize=11, alignment=2, textColor=teal))
        flowables.append(tot_p)
        flowables.append(Spacer(1, 20))

        footer_text = f"Payment terms: {s['payment_terms']}. Electronic remittance to Account ending: <b>{s['bank_last4']}</b>. Contact: {s['vendor_email']}"
        flowables.append(Paragraph(footer_text, ParagraphStyle("FootT", fontName="Helvetica", fontSize=8, textColor=colors.HexColor("#64748b"))))

    elif layout == "industrial_bold":
        # 3. Industrial Bold (Apex Industrial Supply Co)
        orange = colors.HexColor("#ea580c")
        charcoal = colors.HexColor("#1f2937")

        top_tbl = Table(
            [
                [
                    Paragraph(f"<font size=20><b>{s['vendor_name']}</b></font><br/>{s['vendor_address']}<br/>Tax ID: {s['vendor_tax_id']}<br/>Tel: {s['vendor_phone']}", ParagraphStyle("IB1", fontName="Helvetica", fontSize=9, leading=13)),
                    Paragraph(f"<font size=16 color='#ea580c'><b>COMMERCIAL INVOICE</b></font><br/><b>Invoice No:</b> {s['invoice_number']}<br/><b>Invoice Date:</b> {s['invoice_date_raw']}<br/><b>Due Date:</b> {s['due_date_raw']}<br/><b>PO Number:</b> {s['po_reference']}", ParagraphStyle("IB2", fontName="Helvetica", fontSize=9, leading=14)),
                ]
            ],
            colWidths=[310, 230],
        )
        top_tbl.setStyle(TableStyle([
            ("BOX", (0, 0), (-1, -1), 1.5, charcoal),
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#fafaf9")),
            ("PADDING", (0, 0), (-1, -1), 8),
        ]))
        flowables.append(top_tbl)
        flowables.append(Spacer(1, 15))

        if s.get("page_notes"):
            flowables.append(Paragraph(f"<b>Notice:</b> {s['page_notes']}", ParagraphStyle("NoteI", fontName="Helvetica-Oblique", fontSize=9, textColor=charcoal)))
            flowables.append(Spacer(1, 8))

        # Industrial Grid Table
        data = [["Line", "Item / Part #", "Description", "Shipped", "Unit Price", "Net Amount"]]
        for item in s["line_items"]:
            data.append([
                str(item["line"]),
                item["sku"] or "N/A",
                item["description"],
                f"{item['quantity']:.0f}",
                f"${item['unit_price']:,.2f}",
                f"${item['line_total']:,.2f}",
            ])

        it = Table(data, colWidths=[35, 90, 215, 55, 75, 70])
        it.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), charcoal),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("ALIGN", (3, 0), (-1, -1), "RIGHT"),
            ("GRID", (0, 0), (-1, -1), 1, charcoal),
            ("PADDING", (0, 0), (-1, -1), 6),
        ]))
        flowables.append(it)
        flowables.append(Spacer(1, 10))

        tot_data = [
            ["Subtotal Net:", f"${s['subtotal']:,.2f}"],
            [f"Sales Tax ({s['tax_rate']*100:.0f}%):", f"${s['tax_amount']:,.2f}"],
            ["INVOICE TOTAL USD:", f"${s['total_amount']:,.2f}"],
        ]
        tot_tbl = Table(tot_data, colWidths=[420, 120])
        tot_tbl.setStyle(TableStyle([
            ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
            ("FONTNAME", (0, 0), (-1, 1), "Helvetica"),
            ("FONTNAME", (0, 2), (-1, 2), "Helvetica-Bold"),
            ("FONTSIZE", (0, 2), (-1, 2), 11),
            ("BACKGROUND", (0, 2), (-1, 2), colors.HexColor("#ffedd5")),
            ("BOX", (0, 2), (-1, 2), 1, orange),
        ]))
        flowables.append(tot_tbl)
        flowables.append(Spacer(1, 20))

        flowables.append(Paragraph(f"Wire Remittance: Bank Account ending <b>{s['bank_last4']}</b>. Payment Terms: {s['payment_terms']}.", ParagraphStyle("IBFoot", fontName="Helvetica", fontSize=8)))

    elif layout in ("office_classic", "shipping_waybill"):
        # 4. Office Supplies (Crestline E2) & Shipping Freight Note (Meridian E3)
        slate = colors.HexColor("#334155")
        h_style = ParagraphStyle("H1O", fontName="Helvetica-Bold", fontSize=18, textColor=slate, leading=22)
        body = ParagraphStyle("BodyO", fontName="Helvetica", fontSize=9, leading=13)

        if s["id"] == "E3":
            inv_num_display = "____________________"
            date_display = "&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;"
            po_display = f"{s['other_references'][0]}" if s.get("other_references") else "N/A"
        else:
            inv_num_display = s["invoice_number"] or "N/A"
            date_display = s["invoice_date_raw"] or "N/A"
            po_display = s["po_reference"] or (f"Ref: {s['other_references'][0]}" if s.get("other_references") else "N/A")

        tbl = Table(
            [
                [
                    Paragraph(f"<b>{s['vendor_name']}</b><br/>{s['vendor_address']}<br/>Tax ID: {s['vendor_tax_id']}<br/>Phone: {s['vendor_phone']}", body),
                    Paragraph(f"<b>INVOICE / BILL</b><br/><b>Ref #:</b> {inv_num_display}<br/><b>Date:</b> {date_display}<br/><b>PO / Order:</b> {po_display}", body),
                ]
            ],
            colWidths=[310, 230],
        )
        flowables.append(tbl)
        flowables.append(Spacer(1, 15))

        data = [["Item", "Description", "Qty", "Price", "Amount ($)"]]
        for item in s["line_items"]:
            data.append([
                str(item["line"]),
                item["description"],
                str(item["quantity"]),
                f"${item['unit_price']:,.2f}",
                f"${item['line_total']:,.2f}",
            ])

        t = Table(data, colWidths=[35, 305, 50, 75, 75])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e2e8f0")),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("ALIGN", (2, 0), (-1, -1), "RIGHT"),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#94a3b8")),
            ("PADDING", (0, 0), (-1, -1), 6),
        ]))
        flowables.append(t)
        flowables.append(Spacer(1, 10))

        tot_data = [
            ["Subtotal:", f"${s['subtotal']:,.2f}"],
            ["Tax Amount:", f"${s['tax_amount']:,.2f}"],
            ["Total Payable:", f"${s['total_amount']:,.2f}"],
        ]
        tot_tbl = Table(tot_data, colWidths=[420, 120])
        tot_tbl.setStyle(TableStyle([
            ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
            ("FONTNAME", (0, 2), (-1, 2), "Helvetica-Bold"),
            ("FONTSIZE", (0, 2), (-1, 2), 10),
        ]))
        flowables.append(tot_tbl)

        if s.get("page_notes"):
            flowables.append(Spacer(1, 15))
            flowables.append(Paragraph(f"<i>Notes: {s['page_notes']}</i>", body))

    return flowables


def generate_digital_pdf(s: dict, output_path: Path) -> bytes:
    """Generate deterministic digital PDF using reportlab."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=letter,
        leftMargin=36,
        rightMargin=36,
        topMargin=36,
        bottomMargin=36,
    )
    flowables = _build_pdf_flowables(s)
    doc.build(flowables)
    pdf_bytes = buf.getvalue()

    if not s.get("is_image_only"):
        output_path.write_bytes(pdf_bytes)

    return pdf_bytes


def generate_image_only_pdf(s: dict, digital_bytes: bytes, output_path: Path, seed: int = 42) -> None:
    """Render digital PDF to image using PyMuPDF and Pillow,

    apply realistic scan/photo degradation, and save as image-only PDF with zero text layer.
    Uses reportlab canvas with invariant=1 for deterministic file hashes.
    """
    random.seed(seed)
    rng = np.random.default_rng(seed)

    # Open rendered digital PDF with PyMuPDF
    with fitz.open(stream=digital_bytes, filetype="pdf") as doc:
        page = doc[0]

        # Search for "Date:" location for E3 before rasterisation
        date_rect = None
        if s["id"] == "E3":
            date_rects = page.search_for("Date:")
            if date_rects:
                date_rect = date_rects[0]

        pix = page.get_pixmap(dpi=150)
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
    w, h = img.size

    x = np.linspace(0, 1, w, endpoint=False)
    y = np.linspace(0, 1, h, endpoint=False)
    xv, yv = np.meshgrid(x, y)

    if s["id"] == "E2":
        # Office flatbed scan degradation (fully readable)
        # 1. Faint uneven grey shading (lighting gradient across glass)
        shading = 1.0 - 0.04 * (0.6 * yv + 0.4 * xv)
        # Faint horizontal scan lines
        bands = 1.0 + 0.012 * np.sin(yv * 36 * np.pi)
        shading = (shading * bands).astype(np.float32)

        arr = np.array(img, dtype=np.float32)
        arr = arr * shading[:, :, np.newaxis]

        # 2. Light scan noise
        noise = rng.normal(0, 3.0, arr.shape).astype(np.float32)
        arr = np.clip(arr + noise, 0, 255).astype(np.uint8)
        img = Image.fromarray(arr)

        # 3. Slight mechanical feeder rotation (-0.4 deg) and slight blur
        img = img.rotate(-0.4, resample=Image.BICUBIC, expand=True, fillcolor=(248, 248, 246))
        img = img.filter(ImageFilter.GaussianBlur(radius=0.35))

    elif s["id"] == "E3":
        # Poor phone photo scan
        # 1. Draw grey ink smudge over the Date value area (no readable characters)
        if date_rect is not None:
            draw = ImageDraw.Draw(img)
            scale = 150.0 / 72.0
            x_start = int((date_rect.x1 + 4) * scale)
            y_mid = int(((date_rect.y0 + date_rect.y1) / 2) * scale)
            smudge_w = int(115 * scale)
            smudge_h = int(14 * scale)

            r = random.Random(seed)
            # Irregular layered grey smudge blobs
            for _ in range(14):
                bx = x_start + r.randint(0, smudge_w - 25)
                by = y_mid + r.randint(-smudge_h // 2, smudge_h // 2)
                rw = r.randint(25, 50)
                rh = r.randint(8, 16)
                shade = r.randint(85, 135)
                draw.ellipse([bx, by - rh // 2, bx + rw, by + rh // 2], fill=(shade, shade, shade + 2))

            # Overlapping opaque charcoal streaks across center
            for _ in range(10):
                bx = x_start + r.randint(5, smudge_w - 35)
                by = y_mid + r.randint(-4, 4)
                draw.rectangle([bx, by - 5, bx + r.randint(25, 50), by + 5], fill=(75, 75, 80))

        # 2. Faint uneven grey shading (ambient room lighting + vignetting)
        diag_grad = 1.0 - 0.07 * (0.7 * xv + 0.3 * yv)
        cx, cy = 0.5, 0.5
        dist = np.sqrt((xv - cx) ** 2 + (yv - cy) ** 2)
        vignette = 1.0 - 0.06 * (dist / np.max(dist))
        shading = (diag_grad * vignette).astype(np.float32)

        arr = np.array(img, dtype=np.float32)
        # Warm ambient lighting tint on paper
        arr[:, :, 0] *= 0.98   # Red
        arr[:, :, 1] *= 0.97   # Green
        arr[:, :, 2] *= 0.94   # Blue
        arr = arr * shading[:, :, np.newaxis]

        # 3. Phone sensor noise
        noise = rng.normal(0, 5.0, arr.shape).astype(np.float32)
        arr = np.clip(arr + noise, 0, 255).astype(np.uint8)
        img = Image.fromarray(arr)

        # 4. Camera tilt angle (1.2 deg) and slight lens blur
        img = img.rotate(1.2, resample=Image.BICUBIC, expand=True, fillcolor=(242, 238, 228))
        img = img.filter(ImageFilter.GaussianBlur(radius=0.45))

    # Save to deterministic PNG buffer
    img_buf = io.BytesIO()
    img.save(img_buf, format="PNG")
    img_buf.seek(0)

    # Convert dimensions from pixels (at 150 dpi) to points (72 points/inch)
    w_pts = (img.width / 150.0) * 72.0
    h_pts = (img.height / 150.0) * 72.0

    # Save as image-only PDF using invariant canvas
    c = canvas.Canvas(str(output_path), pagesize=(w_pts, h_pts), invariant=1)
    c.drawImage(ImageReader(img_buf), 0, 0, width=w_pts, height=h_pts)
    c.save()


# ── Extraction Fixtures (Exact B7 Schema) ────────────────────────────────

def generate_fixture_json(s: dict, output_path: Path) -> dict:
    """Generate expected B7 extraction JSON fixture."""
    fixture = {
        "vendor_name": s["vendor_name"],
        "vendor_tax_id": s["vendor_tax_id"],
        "vendor_email": s["vendor_email"],
        "bank_account_last4": s["bank_last4"],
        "bank_iban": None,
        "invoice_number": s["invoice_number"],
        "invoice_date": s["invoice_date"],
        "invoice_date_raw": s["invoice_date_raw"],
        "due_date": s["due_date"],
        "due_date_raw": s["due_date_raw"],
        "currency": s["currency"],
        "po_reference": s["po_reference"],
        "other_references": s["other_references"],
        "line_items": [
            {
                "description": item["description"],
                "sku": item.get("sku"),
                "quantity": item["quantity"],
                "unit_price": item["unit_price"],
                "line_total": item["line_total"],
            }
            for item in s["line_items"]
        ],
        "subtotal": s["subtotal"],
        "tax_amount": s["tax_amount"],
        "tax_rate": s["tax_rate"],
        "tax_mode": s["tax_mode"],
        "discount": s["discount"],
        "total_amount": s["total_amount"],
        "payment_terms": s["payment_terms"],
        "page_notes": s["page_notes"],
        "evidence": {
            "vendor_name": s["vendor_name"],
            "invoice_number": s["invoice_number"] or "",
            "total_amount": f"{s['total_amount']:,.2f}",
        },
        "document_quality": s["document_quality"],
        "missing_fields": s["missing_fields"],
    }

    output_path.write_text(json.dumps(fixture, indent=2), encoding="utf-8")
    return fixture


# ── Manifest & SHA-256 Hashes ───────────────────────────────────────────

def get_file_sha256(path: Path) -> str:
    """Calculate SHA-256 hash of a file."""
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def generate_all() -> dict:
    """Generate all sample PDFs, extraction fixtures, and manifest.json."""
    SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)

    manifest = {}

    for s_id, s in SCENARIOS.items():
        pdf_path = SAMPLES_DIR / s["file_name"]
        digital_bytes = generate_digital_pdf(s, pdf_path)

        if s["is_image_only"]:
            generate_image_only_pdf(s, digital_bytes, pdf_path, seed=42)

        # Extraction JSON fixture
        json_path = FIXTURES_DIR / f"{s_id}.json"
        generate_fixture_json(s, json_path)

        # Manifest entry
        sha = get_file_sha256(pdf_path)
        manifest[sha] = s_id

    # Write manifest.json
    manifest_path = SAMPLES_DIR / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    return manifest


if __name__ == "__main__":
    m = generate_all()
    print(f"Generated {len(m)} sample invoices and fixtures. Manifest written to {SAMPLES_DIR / 'manifest.json'}")
