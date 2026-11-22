"""SQLite data-access layer.

Schema: vendors, vendor_aliases, purchase_orders, po_lines,
        goods_receipts, ledger, ledger_lines, runs, run_checks,
        run_events, review_actions.
"""

import json
import sqlite3
import os
from pathlib import Path

_DEFAULT_DB = str(Path(__file__).resolve().parent.parent / "data.db")


# ── Connection ──────────────────────────────────────────────────────────

def get_connection(db_path: str | None = None) -> sqlite3.Connection:
    """Return a connection with WAL mode (file DBs) and row-factory."""
    path = db_path or os.environ.get("DB_PATH", _DEFAULT_DB)
    conn = sqlite3.connect(path)
    if path != ":memory:":
        conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


# ── Schema ──────────────────────────────────────────────────────────────

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS vendors (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'active',
    tax_id          TEXT,
    bank_last4      TEXT,
    terms           TEXT,
    callback_phone  TEXT,
    domain          TEXT
);

CREATE TABLE IF NOT EXISTS vendor_aliases (
    vendor_id   TEXT NOT NULL REFERENCES vendors(id),
    alias       TEXT NOT NULL,
    PRIMARY KEY (vendor_id, alias)
);

CREATE TABLE IF NOT EXISTS purchase_orders (
    po_number   TEXT PRIMARY KEY,
    vendor_id   TEXT NOT NULL REFERENCES vendors(id),
    tax_rate    REAL NOT NULL,
    net         REAL NOT NULL,
    gross       REAL NOT NULL,
    grn_needed  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS po_lines (
    po_number   TEXT NOT NULL REFERENCES purchase_orders(po_number),
    line_number INTEGER NOT NULL,
    description TEXT NOT NULL,
    sku         TEXT,
    quantity    REAL NOT NULL,
    unit_price  REAL NOT NULL,
    PRIMARY KEY (po_number, line_number)
);

CREATE TABLE IF NOT EXISTS goods_receipts (
    po_number           TEXT NOT NULL,
    line_number         INTEGER NOT NULL,
    quantity_received   REAL NOT NULL,
    PRIMARY KEY (po_number, line_number),
    FOREIGN KEY (po_number, line_number)
        REFERENCES po_lines(po_number, line_number)
);

CREATE TABLE IF NOT EXISTS ledger (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_number  TEXT NOT NULL,
    vendor_id       TEXT NOT NULL REFERENCES vendors(id),
    po_number       TEXT REFERENCES purchase_orders(po_number),
    net             REAL NOT NULL,
    gross           REAL NOT NULL,
    invoice_date    TEXT NOT NULL,
    posted_at       TEXT NOT NULL,
    run_id          TEXT
);

CREATE TABLE IF NOT EXISTS ledger_lines (
    ledger_id           INTEGER NOT NULL REFERENCES ledger(id),
    po_line_number      INTEGER NOT NULL,
    quantity_invoiced   REAL NOT NULL,
    net_amount          REAL NOT NULL,
    PRIMARY KEY (ledger_id, po_line_number)
);

CREATE TABLE IF NOT EXISTS runs (
    run_id              TEXT PRIMARY KEY,
    invoice_number      TEXT,
    vendor_id           TEXT,
    vendor_name         TEXT,
    po_number           TEXT,
    status              TEXT NOT NULL,
    severity            TEXT,
    headline            TEXT,
    net                 REAL,
    gross               REAL,
    currency            TEXT DEFAULT 'USD',
    invoice_date        TEXT,
    due_date            TEXT,
    pay_by              TEXT,
    next_action         TEXT,
    email_draft         TEXT,
    extraction_json     TEXT,
    processing_time_ms  REAL,
    created_at          TEXT NOT NULL,
    seeded              INTEGER NOT NULL DEFAULT 0,
    file_hash           TEXT,
    file_name           TEXT
);

CREATE TABLE IF NOT EXISTS run_checks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      TEXT NOT NULL REFERENCES runs(run_id),
    check_id    TEXT NOT NULL,
    check_name  TEXT NOT NULL,
    stage       TEXT NOT NULL,
    result      TEXT NOT NULL,
    severity    TEXT,
    message     TEXT,
    evidence    TEXT
);

CREATE TABLE IF NOT EXISTS run_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          TEXT NOT NULL REFERENCES runs(run_id),
    stage_number    INTEGER NOT NULL,
    stage_name      TEXT NOT NULL,
    event_type      TEXT NOT NULL,
    data_json       TEXT,
    duration_ms     REAL,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS review_actions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      TEXT NOT NULL REFERENCES runs(run_id),
    action      TEXT NOT NULL,
    note        TEXT,
    reviewer    TEXT DEFAULT 'demo_user',
    created_at  TEXT NOT NULL
);
"""


def create_tables(conn: sqlite3.Connection) -> None:
    """Create all tables (idempotent)."""
    conn.executescript(_SCHEMA)
    conn.commit()


# ── Vendor queries ──────────────────────────────────────────────────────

def get_vendor(conn: sqlite3.Connection, vendor_id: str):
    return conn.execute(
        "SELECT * FROM vendors WHERE id = ?", (vendor_id,)
    ).fetchone()


def get_all_vendors(conn: sqlite3.Connection):
    return conn.execute("SELECT * FROM vendors ORDER BY id").fetchall()


def get_vendor_aliases(conn: sqlite3.Connection, vendor_id: str) -> list[str]:
    rows = conn.execute(
        "SELECT alias FROM vendor_aliases WHERE vendor_id = ? ORDER BY alias",
        (vendor_id,),
    ).fetchall()
    return [r["alias"] for r in rows]


# ── PO queries ──────────────────────────────────────────────────────────

def get_purchase_order(conn: sqlite3.Connection, po_number: str):
    return conn.execute(
        "SELECT * FROM purchase_orders WHERE po_number = ?", (po_number,)
    ).fetchone()


def get_all_purchase_orders(conn: sqlite3.Connection):
    return conn.execute(
        "SELECT * FROM purchase_orders ORDER BY po_number"
    ).fetchall()


def get_po_lines(conn: sqlite3.Connection, po_number: str):
    return conn.execute(
        "SELECT * FROM po_lines WHERE po_number = ? ORDER BY line_number",
        (po_number,),
    ).fetchall()


def get_goods_receipt(conn: sqlite3.Connection, po_number: str, line_number: int):
    return conn.execute(
        "SELECT * FROM goods_receipts WHERE po_number = ? AND line_number = ?",
        (po_number, line_number),
    ).fetchone()


def get_po_remaining_net(conn: sqlite3.Connection, po_number: str) -> float:
    """PO net minus sum of posted invoice nets for that PO."""
    row = conn.execute(
        """SELECT po.net - COALESCE(
               (SELECT SUM(l.net) FROM ledger l
                WHERE l.po_number = po.po_number), 0
           ) AS remaining
           FROM purchase_orders po
           WHERE po.po_number = ?""",
        (po_number,),
    ).fetchone()
    return float(row["remaining"]) if row else 0.0


def get_po_line_remaining_qty(
    conn: sqlite3.Connection, po_number: str, line_number: int
) -> float:
    """Received quantity minus sum of invoiced quantity for a PO line."""
    row = conn.execute(
        """SELECT gr.quantity_received - COALESCE(
               (SELECT SUM(ll.quantity_invoiced)
                FROM ledger_lines ll
                JOIN ledger l ON l.id = ll.ledger_id
                WHERE l.po_number = ? AND ll.po_line_number = ?), 0
           ) AS remaining
           FROM goods_receipts gr
           WHERE gr.po_number = ? AND gr.line_number = ?""",
        (po_number, line_number, po_number, line_number),
    ).fetchone()
    return float(row["remaining"]) if row else 0.0


# ── Ledger ──────────────────────────────────────────────────────────────

def get_ledger_entries(
    conn: sqlite3.Connection,
    po_number: str | None = None,
    vendor_id: str | None = None,
    invoice_number: str | None = None,
) -> list:
    sql = "SELECT * FROM ledger WHERE 1=1"
    params: list = []
    if po_number:
        sql += " AND po_number = ?"
        params.append(po_number)
    if vendor_id:
        sql += " AND vendor_id = ?"
        params.append(vendor_id)
    if invoice_number:
        sql += " AND invoice_number = ?"
        params.append(invoice_number)
    return conn.execute(sql, params).fetchall()


def get_ledger_lines(conn: sqlite3.Connection, ledger_id: int) -> list:
    return conn.execute(
        "SELECT * FROM ledger_lines WHERE ledger_id = ? ORDER BY po_line_number",
        (ledger_id,),
    ).fetchall()


def post_to_ledger(
    conn: sqlite3.Connection,
    *,
    invoice_number: str,
    vendor_id: str,
    po_number: str,
    net: float,
    gross: float,
    invoice_date: str,
    posted_at: str,
    run_id: str | None = None,
    lines: list[dict] | None = None,
) -> int:
    """Insert a posted invoice into the ledger with optional line detail."""
    cur = conn.execute(
        """INSERT INTO ledger
           (invoice_number, vendor_id, po_number, net, gross,
            invoice_date, posted_at, run_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (invoice_number, vendor_id, po_number, net, gross,
         invoice_date, posted_at, run_id),
    )
    ledger_id = cur.lastrowid
    for line in (lines or []):
        conn.execute(
            """INSERT INTO ledger_lines
               (ledger_id, po_line_number, quantity_invoiced, net_amount)
               VALUES (?, ?, ?, ?)""",
            (ledger_id, line["po_line_number"],
             line["quantity_invoiced"], line["net_amount"]),
        )
    conn.commit()
    return ledger_id


# ── Runs ────────────────────────────────────────────────────────────────

def get_run(conn: sqlite3.Connection, run_id: str):
    return conn.execute(
        "SELECT * FROM runs WHERE run_id = ?", (run_id,)
    ).fetchone()


def get_runs(
    conn: sqlite3.Connection,
    status: str | None = None,
    seeded: bool | None = None,
) -> list:
    sql = "SELECT * FROM runs WHERE 1=1"
    params: list = []
    if status:
        sql += " AND status = ?"
        params.append(status)
    if seeded is not None:
        sql += " AND seeded = ?"
        params.append(1 if seeded else 0)
    sql += " ORDER BY created_at DESC"
    return conn.execute(sql, params).fetchall()


def count_runs_by_status(
    conn: sqlite3.Connection, seeded: bool | None = None
) -> dict[str, int]:
    sql = "SELECT status, COUNT(*) AS cnt FROM runs"
    params: list = []
    if seeded is not None:
        sql += " WHERE seeded = ?"
        params.append(1 if seeded else 0)
    sql += " GROUP BY status"
    return {r["status"]: r["cnt"] for r in conn.execute(sql, params).fetchall()}


def get_run_checks(conn: sqlite3.Connection, run_id: str) -> list:
    return conn.execute(
        "SELECT * FROM run_checks WHERE run_id = ? ORDER BY id",
        (run_id,),
    ).fetchall()


def save_run(
    conn: sqlite3.Connection,
    run: dict,
    checks: list[dict] | None = None,
) -> str:
    """Insert a run record and its checks."""
    defaults = {
        "severity": None, "headline": None, "net": None,
        "gross": None, "currency": "USD", "invoice_date": None,
        "due_date": None, "pay_by": None, "next_action": None,
        "email_draft": None, "extraction_json": None,
        "processing_time_ms": None, "seeded": 0,
        "file_hash": None, "file_name": None,
    }
    r = {**defaults, **run}
    conn.execute(
        """INSERT INTO runs
           (run_id, invoice_number, vendor_id, vendor_name, po_number,
            status, severity, headline, net, gross, currency,
            invoice_date, due_date, pay_by, next_action, email_draft,
            extraction_json, processing_time_ms, created_at, seeded,
            file_hash, file_name)
           VALUES (:run_id, :invoice_number, :vendor_id, :vendor_name,
                   :po_number, :status, :severity, :headline, :net,
                   :gross, :currency, :invoice_date, :due_date,
                   :pay_by, :next_action, :email_draft,
                   :extraction_json, :processing_time_ms, :created_at,
                   :seeded, :file_hash, :file_name)""",
        r,
    )
    for c in (checks or []):
        ev = c.get("evidence")
        if isinstance(ev, (dict, list)):
            ev = json.dumps(ev)
        conn.execute(
            """INSERT INTO run_checks
               (run_id, check_id, check_name, stage, result,
                severity, message, evidence)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (r["run_id"], c["check_id"], c["check_name"], c["stage"],
             c["result"], c.get("severity"), c.get("message"),
             ev),
        )
    conn.commit()
    return r["run_id"]


# ── Reset ───────────────────────────────────────────────────────────────

def clear_all_data(conn: sqlite3.Connection) -> None:
    """Delete all rows from every table (respects FK order)."""
    for table in (
        "review_actions", "run_events", "run_checks", "runs",
        "ledger_lines", "ledger", "goods_receipts", "po_lines",
        "purchase_orders", "vendor_aliases", "vendors",
    ):
        conn.execute(f"DELETE FROM {table}")
    conn.commit()


def is_db_seeded(conn: sqlite3.Connection) -> bool:
    """True if the vendors table exists and has rows."""
    try:
        row = conn.execute("SELECT COUNT(*) AS cnt FROM vendors").fetchone()
        return row["cnt"] > 0
    except sqlite3.OperationalError:
        return False
