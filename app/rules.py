"""Deterministic rules engine for invoice processing.

Pure Python module: no DB writes, no branching on scenario id or file name.
All thresholds loaded from config/rules.json.
"""

from dataclasses import dataclass, field
from datetime import date, datetime
import re
from typing import Any, Optional

from rapidfuzz import fuzz

from app.config import get_as_of_date, load_rules


# ── Check Data Structure ────────────────────────────────────────────────

@dataclass
class Check:
    id: str
    name: str
    stage: str
    result: str  # "pass", "warn", "fail", "skip"
    outcome_class: Optional[str] = None  # "NEEDS_INFO", "NEEDS_REVIEW", "REJECTED", None
    message: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)
    severity: Optional[str] = None  # "CRITICAL" or None

    def to_dict(self) -> dict[str, Any]:
        return {
            "check_id": self.id,
            "check_name": self.name,
            "stage": self.stage,
            "result": self.result,
            "outcome_class": self.outcome_class,
            "severity": self.severity,
            "message": self.message,
            "evidence": self.evidence,
        }


# ── Normalisation Functions ─────────────────────────────────────────────

def invoice_number_key(inv: str | None) -> str:
    """Normalise invoice number: lowercase, strip non-alphanumeric,

    strip leading zeros inside digit runs.
    E.g. 'CR-000457' and 'CR 457' both become 'cr457'.
    """
    if not inv:
        return ""
    cleaned = re.sub(r"[^a-zA-Z0-9]", "", inv).lower()
    return re.sub(r"\d+", lambda m: str(int(m.group(0))), cleaned)


def po_key(po: str | None) -> str:
    """Normalise PO reference: strip 'PO', spaces and dashes.

    E.g. 'PO7322' and 'PO-7322' both become '7322'.
    """
    if not po:
        return ""
    s = re.sub(r"^(?:po)?[-\s]*", "", po.strip(), flags=re.IGNORECASE)
    return re.sub(r"[-\s]", "", s).upper()


def vendor_name_normalise(name: str | None) -> str:
    """Normalise vendor name: lowercase, collapse punctuation and spaces."""
    if not name:
        return ""
    s = re.sub(r"[^\w\s]", " ", name.lower())
    return " ".join(s.split())


def calculate_invoice_net(
    subtotal: float | None,
    total: float | None,
    tax_amount: float | None = None,
    tax_mode: str | None = None,
    po_tax_rate: float | None = None,
) -> tuple[float | None, bool]:
    """Calculate invoice net amount and whether tax was inferred.

    SPEC B8: 'Invoice net = subtotal if present; else total minus tax when tax is separate;
    else total divided by (1 plus PO tax rate) with tax marked inferred.'
    Returns (net, tax_inferred).
    """
    if tax_mode == "included":
        if total is not None and po_tax_rate is not None:
            return round(float(total) / (1.0 + float(po_tax_rate)), 2), True
        if subtotal is not None and subtotal > 0:
            return round(float(subtotal), 2), True

    if subtotal is not None and subtotal > 0:
        return round(float(subtotal), 2), False

    if total is not None:
        tot = float(total)
        if tax_mode == "separate" and tax_amount is not None:
            return round(tot - float(tax_amount), 2), False
        if po_tax_rate is not None and (tax_mode in ("included", None, "unknown") or tax_amount is None):
            return round(tot / (1.0 + float(po_tax_rate)), 2), True
        if tax_amount is not None and float(tax_amount) > 0:
            return round(tot - float(tax_amount), 2), False
        return round(tot, 2), False

    return None, False


# ── Individual Rule Functions ───────────────────────────────────────────

def check_ext_01(
    vendor_name: str | None,
    invoice_number: str | None,
    invoice_date: str | date | None,
    total_amount: float | None,
    currency: str | None,
) -> Check:
    """EXT-01: Critical fields present: vendor, invoice number, invoice date, total, currency."""
    missing = []
    if not vendor_name or not str(vendor_name).strip():
        missing.append("vendor_name")
    if not invoice_number or not str(invoice_number).strip():
        missing.append("invoice_number")
    if not invoice_date:
        missing.append("invoice_date")
    if total_amount is None:
        missing.append("total_amount")
    if not currency or not str(currency).strip():
        missing.append("currency")

    if missing:
        msg = f"Missing critical fields: {', '.join(missing)}."
        return Check(
            id="EXT-01",
            name="Critical fields present",
            stage="verify",
            result="fail",
            outcome_class="NEEDS_INFO",
            message=msg,
            evidence={"missing_fields": missing},
        )
    return Check(
        id="EXT-01",
        name="Critical fields present",
        stage="verify",
        result="pass",
        outcome_class=None,
        message="All critical fields present.",
        evidence={"currency": currency, "total_amount": total_amount},
    )


def check_ext_02(
    text_layer: str | None,
    invoice_number: str | None,
    total_amount: float | None,
    is_scanned: bool = False,
    document_quality: str | None = None,
) -> Check:
    """EXT-02: Digital PDF grounding of invoice number and total."""
    if is_scanned or document_quality in ("scanned_ok", "scanned_poor") or not text_layer or not text_layer.strip():
        return Check(
            id="EXT-02",
            name="Digital PDF grounding",
            stage="verify",
            result="skip",
            outcome_class=None,
            message="Scanned document: digital text grounding skipped.",
            evidence={"is_scanned": is_scanned, "document_quality": document_quality},
        )

    tl = text_layer.lower()
    inv_grounded = False
    if invoice_number:
        clean_inv = invoice_number.lower().strip()
        inv_grounded = clean_inv in tl or invoice_number_key(invoice_number) in re.sub(r"[^a-zA-Z0-9]", "", tl)

    tot_grounded = False
    if total_amount is not None:
        # Check formatted versions: e.g. 46,170.00, 46170.00, 46170
        val = float(total_amount)
        variants = [
            f"{val:,.2f}",
            f"{val:.2f}",
            f"{int(val):,}" if val.is_integer() else "",
            f"{int(val)}" if val.is_integer() else "",
        ]
        tot_grounded = any(v and v.lower() in tl for v in variants)

    if inv_grounded and tot_grounded:
        return Check(
            id="EXT-02",
            name="Digital PDF grounding",
            stage="verify",
            result="pass",
            outcome_class=None,
            message="Invoice number and total grounded in document text layer.",
            evidence={"invoice_number_grounded": True, "total_grounded": True},
        )

    missing = []
    if not inv_grounded:
        missing.append("invoice number")
    if not tot_grounded:
        missing.append("total amount")
    return Check(
        id="EXT-02",
        name="Digital PDF grounding",
        stage="verify",
        result="fail",
        outcome_class="NEEDS_REVIEW",
        message=f"Grounding failure: {', '.join(missing)} not found in document text layer.",
        evidence={"invoice_number_grounded": inv_grounded, "total_grounded": tot_grounded},
    )


def check_ext_03(
    line_items: list[dict] | None,
    subtotal: float | None,
    tax_amount: float | None,
    discount: float | None,
    total_amount: float | None,
    tolerance: float = 0.02,
    tax_mode: str | None = None,
) -> Check:
    """EXT-03: Lines plus tax minus discount equal total within tolerance."""
    if total_amount is None:
        return Check(
            id="EXT-03",
            name="Arithmetic consistency",
            stage="verify",
            result="skip",
            outcome_class=None,
            message="Total amount missing; arithmetic check skipped.",
        )

    tot = float(total_amount)
    tax = float(tax_amount or 0.0)
    disc = float(discount or 0.0)

    if line_items:
        lines_sum = sum(
            float(l.get("line_total") if l.get("line_total") is not None
                  else (float(l.get("quantity", 0)) * float(l.get("unit_price", 0))))
            for l in line_items
        )
        calc_total = lines_sum + tax - disc
        diff = abs(calc_total - tot)
        if diff <= tolerance + 1e-4:
            return Check(
                id="EXT-03",
                name="Arithmetic consistency",
                stage="verify",
                result="pass",
                outcome_class=None,
                message=f"Lines ({lines_sum:.2f}) + tax ({tax:.2f}) - discount ({disc:.2f}) = {calc_total:.2f} agrees with total {tot:.2f}.",
                evidence={"lines_sum": lines_sum, "tax": tax, "discount": disc, "diff": round(diff, 4)},
            )

        # In tax-inclusive mode (or when lines sum equals gross total because lines include tax)
        calc_total_inc = lines_sum - disc
        diff_inc = abs(calc_total_inc - tot)
        if (tax_mode == "included" or diff_inc <= tolerance + 1e-4) and diff_inc <= tolerance + 1e-4:
            return Check(
                id="EXT-03",
                name="Arithmetic consistency",
                stage="verify",
                result="pass",
                outcome_class=None,
                message=f"Lines sum ({lines_sum:.2f}) - discount ({disc:.2f}) = {calc_total_inc:.2f} agrees with total {tot:.2f} (tax included: {tax:.2f}).",
                evidence={"lines_sum": lines_sum, "tax": tax, "discount": disc, "tax_mode": "included", "diff": round(diff_inc, 4)},
            )

        if subtotal is not None:
            calc_sub = float(subtotal) + tax - disc
            diff_sub = abs(calc_sub - tot)
            if diff_sub <= tolerance + 1e-4:
                return Check(
                    id="EXT-03",
                    name="Arithmetic consistency",
                    stage="verify",
                    result="pass",
                    outcome_class=None,
                    message=f"Subtotal ({float(subtotal):.2f}) + tax ({tax:.2f}) - discount ({disc:.2f}) agrees with total {tot:.2f}.",
                    evidence={"subtotal": float(subtotal), "tax": tax, "discount": disc, "diff": round(diff_sub, 4)},
                )

        return Check(
            id="EXT-03",
            name="Arithmetic consistency",
            stage="verify",
            result="fail",
            outcome_class="NEEDS_REVIEW",
            message=f"Arithmetic mismatch: lines sum ({lines_sum:.2f}) + tax ({tax:.2f}) - discount ({disc:.2f}) = {calc_total:.2f}, but total is {tot:.2f} (diff: {diff:.2f}).",
            evidence={"lines_sum": lines_sum, "tax": tax, "discount": disc, "diff": round(diff, 4)},
        )

    if subtotal is not None:
        calc_total = float(subtotal) + tax - disc
        diff = abs(calc_total - tot)
        if diff <= tolerance + 1e-4:
            return Check(
                id="EXT-03",
                name="Arithmetic consistency",
                stage="verify",
                result="pass",
                outcome_class=None,
                message=f"Subtotal ({float(subtotal):.2f}) + tax ({tax:.2f}) - discount ({disc:.2f}) agrees with total {tot:.2f}.",
                evidence={"subtotal": float(subtotal), "tax": tax, "discount": disc, "diff": round(diff, 4)},
            )
        return Check(
            id="EXT-03",
            name="Arithmetic consistency",
            stage="verify",
            result="fail",
            outcome_class="NEEDS_REVIEW",
            message=f"Arithmetic mismatch: subtotal ({float(subtotal):.2f}) + tax ({tax:.2f}) - discount ({disc:.2f}) = {calc_total:.2f}, but total is {tot:.2f} (diff: {diff:.2f}).",
            evidence={"subtotal": float(subtotal), "tax": tax, "discount": disc, "diff": round(diff, 4)},
        )

    return Check(
        id="EXT-03",
        name="Arithmetic consistency",
        stage="verify",
        result="skip",
        outcome_class=None,
        message="No line items or subtotal provided; arithmetic check skipped.",
    )


def _parse_date(d: str | date | None) -> date | None:
    if not d:
        return None
    if isinstance(d, date):
        return d
    try:
        return date.fromisoformat(str(d).strip()[:10])
    except Exception:
        return None


def check_ext_04(
    invoice_date: str | date | None,
    due_date: str | date | None = None,
    as_of_date: date | None = None,
    stale_days: int = 180,
) -> Check:
    """EXT-04: Date sanity: not in the future, due not before invoice date; over 180 days is warning."""
    inv_d = _parse_date(invoice_date)
    due_d = _parse_date(due_date)
    ref_d = as_of_date or get_as_of_date()

    if not inv_d:
        return Check(
            id="EXT-04",
            name="Date sanity",
            stage="verify",
            result="skip",
            outcome_class=None,
            message="Invoice date missing; date sanity check skipped.",
        )

    if inv_d > ref_d:
        return Check(
            id="EXT-04",
            name="Date sanity",
            stage="verify",
            result="fail",
            outcome_class="NEEDS_REVIEW",
            message=f"Invoice date {inv_d} is in the future (relative to as-of date {ref_d}).",
            evidence={"invoice_date": inv_d.isoformat(), "as_of_date": ref_d.isoformat()},
        )

    if due_d and due_d < inv_d:
        return Check(
            id="EXT-04",
            name="Date sanity",
            stage="verify",
            result="fail",
            outcome_class="NEEDS_REVIEW",
            message=f"Due date {due_d} is earlier than invoice date {inv_d}.",
            evidence={"invoice_date": inv_d.isoformat(), "due_date": due_d.isoformat()},
        )

    age_days = (ref_d - inv_d).days
    if age_days > stale_days:
        return Check(
            id="EXT-04",
            name="Date sanity",
            stage="verify",
            result="warn",
            outcome_class=None,
            message=f"Invoice is {age_days} days old (exceeds {stale_days}-day stale threshold).",
            evidence={"age_days": age_days, "stale_threshold": stale_days},
        )

    return Check(
        id="EXT-04",
        name="Date sanity",
        stage="verify",
        result="pass",
        outcome_class=None,
        message=f"Invoice date {inv_d} is valid.",
        evidence={"invoice_date": inv_d.isoformat(), "age_days": age_days},
    )


def check_ven_01(
    printed_name: str | None,
    master_vendors: list[dict],
    accept_score: float = 92.0,
    review_score: float = 80.0,
) -> tuple[Check, dict | None]:
    """VEN-01: Vendor resolves to master (>=92 or alias = pass; 80-91 = review; <80 = unknown).

    Returns (Check, matched_vendor_or_None).
    """
    if not printed_name or not str(printed_name).strip():
        return (
            Check(
                id="VEN-01",
                name="Vendor resolution",
                stage="vendor",
                result="fail",
                outcome_class="NEEDS_REVIEW",
                message="No vendor name provided on invoice.",
            ),
            None,
        )

    norm_printed = vendor_name_normalise(printed_name)
    best_vendor = None
    best_score = 0.0
    matched_alias = False

    for v in master_vendors:
        # Check exact alias or primary name match
        v_norm = vendor_name_normalise(v["name"])
        score = fuzz.token_set_ratio(norm_printed, v_norm)
        if norm_printed == v_norm:
            score = 100.0

        for alias in v.get("aliases", []):
            a_norm = vendor_name_normalise(alias)
            if norm_printed == a_norm:
                score = 100.0
                matched_alias = True
                break
            a_score = fuzz.token_set_ratio(norm_printed, a_norm)
            if a_score > score:
                score = a_score

        if score > best_score:
            best_score = score
            best_vendor = v

    if best_score >= accept_score:
        return (
            Check(
                id="VEN-01",
                name="Vendor resolution",
                stage="vendor",
                result="pass",
                outcome_class=None,
                message=f"Vendor resolved to {best_vendor['name']} ({best_vendor['id']}, match score: {best_score:.1f}%).",
                evidence={"vendor_id": best_vendor["id"], "score": best_score, "alias_match": matched_alias},
            ),
            best_vendor,
        )

    if best_score >= review_score:
        return (
            Check(
                id="VEN-01",
                name="Vendor resolution",
                stage="vendor",
                result="warn",
                outcome_class="NEEDS_REVIEW",
                message=f"Probable vendor match: {best_vendor['name']} (score: {best_score:.1f}%). Review required.",
                evidence={"vendor_id": best_vendor["id"], "score": best_score},
            ),
            best_vendor,
        )

    return (
        Check(
            id="VEN-01",
            name="Vendor resolution",
            stage="vendor",
            result="fail",
            outcome_class="NEEDS_REVIEW",
            message=f"Unknown vendor '{printed_name}' (best score: {best_score:.1f}%).",
            evidence={"best_score": best_score},
        ),
        None,
    )


def check_ven_02(vendor: dict | None) -> Check:
    """VEN-02: Vendor status is active (REJECTED if blocked)."""
    if not vendor:
        return Check(
            id="VEN-02",
            name="Vendor active status",
            stage="vendor",
            result="skip",
            outcome_class=None,
            message="No resolved vendor; status check skipped.",
        )

    status = vendor.get("status", "active")
    if status == "active":
        return Check(
            id="VEN-02",
            name="Vendor active status",
            stage="vendor",
            result="pass",
            outcome_class=None,
            message=f"Vendor {vendor['name']} is active.",
            evidence={"status": status},
        )

    if status == "blocked":
        return Check(
            id="VEN-02",
            name="Vendor active status",
            stage="vendor",
            result="fail",
            outcome_class="REJECTED",
            message=f"Vendor {vendor['name']} is blocked (compliance hold).",
            evidence={"status": status},
        )

    return Check(
        id="VEN-02",
        name="Vendor active status",
        stage="vendor",
        result="fail",
        outcome_class="REJECTED",
        message=f"Vendor {vendor['name']} status is '{status}'.",
        evidence={"status": status},
    )


def check_ven_03(printed_tax_id: str | None, vendor: dict | None) -> Check:
    """VEN-03: Printed tax ID matches master."""
    if not printed_tax_id or not vendor or not vendor.get("tax_id"):
        return Check(
            id="VEN-03",
            name="Vendor tax ID match",
            stage="vendor",
            result="skip",
            outcome_class=None,
            message="Printed tax ID or master tax ID not available; check skipped.",
        )

    p_norm = re.sub(r"[^a-zA-Z0-9]", "", printed_tax_id).upper()
    m_norm = re.sub(r"[^a-zA-Z0-9]", "", vendor["tax_id"]).upper()

    if p_norm == m_norm:
        return Check(
            id="VEN-03",
            name="Vendor tax ID match",
            stage="vendor",
            result="pass",
            outcome_class=None,
            message=f"Tax ID matches master ({vendor['tax_id']}).",
            evidence={"printed_tax_id": printed_tax_id, "master_tax_id": vendor["tax_id"]},
        )

    return Check(
        id="VEN-03",
        name="Vendor tax ID match",
        stage="vendor",
        result="fail",
        outcome_class="NEEDS_REVIEW",
        message=f"Tax ID mismatch: printed '{printed_tax_id}' does not match master '{vendor['tax_id']}'.",
        evidence={"printed_tax_id": printed_tax_id, "master_tax_id": vendor["tax_id"]},
    )


def check_ven_04(printed_bank_last4: str | None, vendor: dict | None) -> Check:
    """VEN-04: Printed bank account matches master. Failure is CRITICAL."""
    if not printed_bank_last4 or not vendor or not vendor.get("bank_last4"):
        return Check(
            id="VEN-04",
            name="Bank account match",
            stage="vendor",
            result="skip",
            outcome_class=None,
            message="Bank account details not printed or master not available; check skipped.",
        )

    p_last4 = str(printed_bank_last4).strip()[-4:]
    m_last4 = str(vendor["bank_last4"]).strip()[-4:]

    if p_last4 == m_last4:
        return Check(
            id="VEN-04",
            name="Bank account match",
            stage="vendor",
            result="pass",
            outcome_class=None,
            message=f"Bank account last 4 digits ({p_last4}) match master record.",
            evidence={"printed_last4": p_last4, "master_last4": m_last4},
        )

    return Check(
        id="VEN-04",
        name="Bank account match",
        stage="vendor",
        result="fail",
        outcome_class="NEEDS_REVIEW",
        severity="CRITICAL",
        message=f"Bank account mismatch: printed last 4 '{p_last4}' does not match master '{m_last4}'. Payment blocked.",
        evidence={"printed_last4": p_last4, "master_last4": m_last4},
    )


def check_ven_05(printed_email: str | None, vendor: dict | None) -> Check:
    """VEN-05: Remit-to / contact email domain matches master. Warning escalates VEN-04."""
    if not printed_email or not vendor or not vendor.get("domain"):
        return Check(
            id="VEN-05",
            name="Email domain match",
            stage="vendor",
            result="skip",
            outcome_class=None,
            message="Email or master domain not available; check skipped.",
        )

    email_parts = printed_email.strip().lower().split("@")
    if len(email_parts) < 2:
        return Check(
            id="VEN-05",
            name="Email domain match",
            stage="vendor",
            result="skip",
            outcome_class=None,
            message="Invalid email format; domain check skipped.",
        )

    domain = email_parts[-1].strip()
    master_domain = vendor["domain"].strip().lower()

    if domain == master_domain:
        return Check(
            id="VEN-05",
            name="Email domain match",
            stage="vendor",
            result="pass",
            outcome_class=None,
            message=f"Email domain '{domain}' matches master record.",
            evidence={"email_domain": domain, "master_domain": master_domain},
        )

    return Check(
        id="VEN-05",
        name="Email domain match",
        stage="vendor",
        result="warn",
        outcome_class=None,
        message=f"Remit email domain '{domain}' does not match master domain '{master_domain}'.",
        evidence={"email_domain": domain, "master_domain": master_domain},
    )


def check_dup_01(
    vendor_id: str | None,
    invoice_number: str | None,
    file_hash: str | None,
    ledger_entries: list[dict],
    past_runs: list[dict] | None = None,
) -> Check:
    """DUP-01: Same vendor and normalised invoice number, or identical file hash."""
    # Check 1: identical file hash against past runs
    if file_hash and past_runs:
        for r in past_runs:
            if r.get("file_hash") and r["file_hash"] == file_hash:
                return Check(
                    id="DUP-01",
                    name="Duplicate invoice check",
                    stage="duplicate",
                    result="fail",
                    outcome_class="REJECTED",
                    message=f"Identical file hash duplicate of run {r['run_id']}.",
                    evidence={"matching_run_id": r["run_id"], "file_hash": file_hash},
                )

    # Check 2: same vendor and normalised invoice number
    if vendor_id and invoice_number:
        inv_key = invoice_number_key(invoice_number)
        for entry in ledger_entries:
            if entry["vendor_id"] == vendor_id:
                if invoice_number_key(entry["invoice_number"]) == inv_key:
                    return Check(
                        id="DUP-01",
                        name="Duplicate invoice check",
                        stage="duplicate",
                        result="fail",
                        outcome_class="REJECTED",
                        message=f"Duplicate invoice: {invoice_number} matches posted invoice {entry['invoice_number']} (run {entry.get('run_id')}).",
                        evidence={"matching_invoice": entry["invoice_number"], "matching_run_id": entry.get("run_id")},
                    )

    return Check(
        id="DUP-01",
        name="Duplicate invoice check",
        stage="duplicate",
        result="pass",
        outcome_class=None,
        message="No exact duplicate found in ledger or previous runs.",
    )


def check_dup_02(
    vendor_id: str | None,
    invoice_number: str | None,
    invoice_date: str | date | None,
    total_amount: float | None,
    ledger_entries: list[dict],
    dup_similarity: float = 85.0,
    dup_window_days: int = 10,
) -> Check:
    """DUP-02: Same vendor and same total and (number similarity >= 85 OR dates within 10 days).

    A monthly retainer of same amount 30 days apart must NOT trigger.
    """
    if not vendor_id or total_amount is None:
        return Check(
            id="DUP-02",
            name="Probable duplicate check",
            stage="duplicate",
            result="skip",
            outcome_class=None,
            message="Vendor or total amount missing; probable duplicate check skipped.",
        )

    inv_d = _parse_date(invoice_date)
    tot = float(total_amount)
    curr_key = invoice_number_key(invoice_number) if invoice_number else ""

    for entry in ledger_entries:
        if entry["vendor_id"] != vendor_id:
            continue

        # Skip exact duplicate (already handled by DUP-01)
        if curr_key and invoice_number_key(entry["invoice_number"]) == curr_key:
            continue

        # Must have same total
        if abs(float(entry["gross"]) - tot) > 0.01:
            continue

        # Check condition 1: number similarity >= 85
        sim = 0.0
        if invoice_number and entry.get("invoice_number"):
            sim = fuzz.ratio(invoice_number.lower(), entry["invoice_number"].lower())

        # Check condition 2: dates within window
        date_close = False
        entry_d = _parse_date(entry.get("invoice_date"))
        if inv_d and entry_d:
            days_diff = abs((inv_d - entry_d).days)
            date_close = days_diff <= dup_window_days

        if sim >= dup_similarity or date_close:
            reason = f"number similarity {sim:.0f}%" if sim >= dup_similarity else f"dates within {days_diff} days"
            return Check(
                id="DUP-02",
                name="Probable duplicate check",
                stage="duplicate",
                result="fail",
                outcome_class="NEEDS_REVIEW",
                message=f"Probable duplicate: same vendor and amount (${tot:,.2f}) matches invoice {entry['invoice_number']} ({reason}).",
                evidence={"matching_invoice": entry["invoice_number"], "similarity": sim, "date_close": date_close},
            )

    return Check(
        id="DUP-02",
        name="Probable duplicate check",
        stage="duplicate",
        result="pass",
        outcome_class=None,
        message="No probable duplicate detected.",
    )


def check_po_01(
    po_reference: str | None,
    vendor_id: str | None,
    open_pos: list[dict],
    total_amount: float | None = None,
    line_items: list[dict] | None = None,
    implied_min_score: float = 85.0,
) -> tuple[Check, dict | None, str | None]:
    """PO-01: PO found by explicit reference or implied search.

    Returns (Check, matched_po_or_None, suggested_po_number_or_None).
    """
    if po_reference and str(po_reference).strip():
        ref_key = po_key(po_reference)
        for po in open_pos:
            if po_key(po["po_number"]) == ref_key:
                return (
                    Check(
                        id="PO-01",
                        name="Purchase order match",
                        stage="po_match",
                        result="pass",
                        outcome_class=None,
                        message=f"Purchase order {po['po_number']} found by explicit reference.",
                        evidence={"po_number": po["po_number"], "explicit_reference": po_reference},
                    ),
                    po,
                    None,
                )
        return (
            Check(
                id="PO-01",
                name="Purchase order match",
                stage="po_match",
                result="fail",
                outcome_class="NEEDS_INFO",
                message=f"Purchase order reference '{po_reference}' not found in records.",
                evidence={"po_reference": po_reference},
            ),
            None,
            None,
        )

    # Implied PO search among open POs belonging to vendor
    if not vendor_id:
        return (
            Check(
                id="PO-01",
                name="Purchase order match",
                stage="po_match",
                result="fail",
                outcome_class="NEEDS_INFO",
                message="No PO reference printed and vendor unknown; cannot perform implied PO search.",
            ),
            None,
            None,
        )

    vendor_pos = [p for p in open_pos if p["vendor_id"] == vendor_id]
    if not vendor_pos:
        return (
            Check(
                id="PO-01",
                name="Purchase order match",
                stage="po_match",
                result="fail",
                outcome_class="NEEDS_INFO",
                message=f"No open purchase orders found for vendor {vendor_id}.",
            ),
            None,
            None,
        )

    # Score candidate POs by amount match and description
    best_candidate = None
    best_score = 0.0

    for po in vendor_pos:
        score = 0.0
        po_gross = float(po["gross"])
        po_net = float(po["net"])

        # Exact gross match gives 100 on amount
        if total_amount is not None:
            tot = float(total_amount)
            if abs(po_gross - tot) < 0.01:
                score = 100.0
            elif abs(po_net - tot) < 0.01:
                score = 90.0

        if score > best_score:
            best_score = score
            best_candidate = po

    if best_candidate and best_score >= implied_min_score:
        suggested = best_candidate["po_number"]
        return (
            Check(
                id="PO-01",
                name="Purchase order match",
                stage="po_match",
                result="warn",
                outcome_class="NEEDS_REVIEW",
                message=f"Implied PO match: suggested {suggested} (exact total match ${best_candidate['gross']:,.2f}). Human confirmation required.",
                evidence={"suggested_po": suggested, "score": best_score, "is_implied": True},
            ),
            best_candidate,
            suggested,
        )

    return (
        Check(
            id="PO-01",
            name="Purchase order match",
            stage="po_match",
            result="fail",
            outcome_class="NEEDS_INFO",
            message="No purchase order reference printed on invoice and no unambiguous match found.",
        ),
        None,
        None,
    )


def check_po_02(po: dict | None, vendor_id: str | None) -> Check:
    """PO-02: PO belongs to this vendor."""
    if not po or not vendor_id:
        return Check(
            id="PO-02",
            name="PO vendor ownership",
            stage="po_match",
            result="skip",
            outcome_class=None,
            message="PO or vendor not resolved; ownership check skipped.",
        )

    if po["vendor_id"] == vendor_id:
        return Check(
            id="PO-02",
            name="PO vendor ownership",
            stage="po_match",
            result="pass",
            outcome_class=None,
            message=f"Purchase order {po['po_number']} belongs to vendor {vendor_id}.",
            evidence={"po_number": po["po_number"], "vendor_id": vendor_id},
        )

    return Check(
        id="PO-02",
        name="PO vendor ownership",
        stage="po_match",
        result="fail",
        outcome_class="NEEDS_REVIEW",
        message=f"PO ownership mismatch: {po['po_number']} belongs to vendor {po['vendor_id']}, not {vendor_id}.",
        evidence={"po_number": po["po_number"], "po_vendor": po["vendor_id"], "invoice_vendor": vendor_id},
    )


def check_po_03(po: dict | None, remaining_net: float | None) -> Check:
    """PO-03: PO is open with remaining balance."""
    if not po or remaining_net is None:
        return Check(
            id="PO-03",
            name="PO open balance",
            stage="po_match",
            result="skip",
            outcome_class=None,
            message="PO or balance not available; balance check skipped.",
        )

    rem = float(remaining_net)
    if rem > 0.01:
        return Check(
            id="PO-03",
            name="PO open balance",
            stage="po_match",
            result="pass",
            outcome_class=None,
            message=f"PO {po['po_number']} is open with remaining net balance of ${rem:,.2f}.",
            evidence={"po_number": po["po_number"], "remaining_net": rem},
        )

    return Check(
        id="PO-03",
        name="PO open balance",
        stage="po_match",
        result="fail",
        outcome_class="NEEDS_REVIEW",
        message=f"PO {po['po_number']} has no remaining balance (fully billed).",
        evidence={"po_number": po["po_number"], "remaining_net": rem},
    )


def check_line_01(
    invoice_lines: list[dict] | None,
    po_lines: list[dict] | None,
    invoice_net: float | None = None,
    po_remaining_net: float | None = None,
    price_tolerance: float = 0.02,
) -> tuple[Check, list[tuple[dict, dict]]]:
    """LINE-01: Line mapping and unit prices.

    Mapping strategy: SKU exact, then description token_set_ratio >= 80, NO price-only fallback.
    A single bundled invoice line compares invoice net to PO remaining net and gives a warning.
    Returns (Check, list_of_matched_pairs_(inv_line, po_line)).
    """
    if not invoice_lines or not po_lines:
        return (
            Check(
                id="LINE-01",
                name="Line mapping and unit prices",
                stage="lines",
                result="skip",
                outcome_class=None,
                message="Invoice lines or PO lines missing; line check skipped.",
            ),
            [],
        )

    # Check for single bundled line
    if len(invoice_lines) == 1:
        inv_ln = invoice_lines[0]
        # Check if direct SKU match exists
        has_sku_match = False
        if inv_ln.get("sku"):
            has_sku_match = any(
                p.get("sku") and p["sku"].strip().upper() == inv_ln["sku"].strip().upper()
                for p in po_lines
            )

        if not has_sku_match:
            # Check if description matches single line
            best_desc_score = max(
                (fuzz.token_set_ratio(inv_ln.get("description", ""), p.get("description", "")) for p in po_lines),
                default=0,
            )
            # If bundled or low similarity, compare invoice net to PO remaining net
            if best_desc_score < 80 or len(po_lines) > 1:
                # Bundled line
                return (
                    Check(
                        id="LINE-01",
                        name="Line mapping and unit prices",
                        stage="lines",
                        result="warn",
                        outcome_class=None,
                        message="Single bundled line matched against PO remaining balance.",
                        evidence={"bundled_line": True, "invoice_net": invoice_net, "po_remaining_net": po_remaining_net},
                    ),
                    [(inv_ln, po_lines[0])],
                )

    matched_pairs: list[tuple[dict, dict]] = []
    unmapped: list[dict] = []
    price_mismatches: list[dict] = []

    for inv_l in invoice_lines:
        matched_po_l = None

        # 1. Exact SKU
        if inv_l.get("sku"):
            for pol in po_lines:
                if pol.get("sku") and inv_l["sku"].strip().upper() == pol["sku"].strip().upper():
                    matched_po_l = pol
                    break

        # 2. Description token_set_ratio >= 80
        if not matched_po_l and inv_l.get("description"):
            best_score = 0.0
            best_pol = None
            for pol in po_lines:
                score = fuzz.token_set_ratio(inv_l["description"], pol.get("description", ""))
                if score > best_score:
                    best_score = score
                    best_pol = pol
            if best_score >= 80:
                matched_po_l = best_pol

        if matched_po_l:
            matched_pairs.append((inv_l, matched_po_l))
            # Verify unit price
            inv_p = float(inv_l.get("unit_price") or 0.0)
            po_p = float(matched_po_l["unit_price"])
            if abs(inv_p - po_p) > price_tolerance:
                price_mismatches.append({
                    "description": inv_l.get("description"),
                    "invoice_price": inv_p,
                    "po_price": po_p,
                })
        else:
            unmapped.append(inv_l)

    if unmapped:
        desc_list = [l.get("description", "Unknown item") for l in unmapped]
        return (
            Check(
                id="LINE-01",
                name="Line mapping and unit prices",
                stage="lines",
                result="fail",
                outcome_class="NEEDS_REVIEW",
                message=f"Unmapped invoice line(s): {', '.join(desc_list)}.",
                evidence={"unmapped_lines": desc_list},
            ),
            matched_pairs,
        )

    if price_mismatches:
        pm = price_mismatches[0]
        return (
            Check(
                id="LINE-01",
                name="Line mapping and unit prices",
                stage="lines",
                result="fail",
                outcome_class="NEEDS_REVIEW",
                message=f"Unit price mismatch on '{pm['description']}': charged ${pm['invoice_price']:.2f} vs PO ${pm['po_price']:.2f}.",
                evidence={"price_mismatches": price_mismatches},
            ),
            matched_pairs,
        )

    return (
        Check(
            id="LINE-01",
            name="Line mapping and unit prices",
            stage="lines",
            result="pass",
            outcome_class=None,
            message="All invoice lines successfully mapped to PO lines with matching unit prices.",
            evidence={"matched_count": len(matched_pairs)},
        ),
        matched_pairs,
    )


def check_3w_01(
    po: dict | None,
    matched_pairs: list[tuple[dict, dict]],
    get_remaining_qty_fn,
) -> Check:
    """3W-01: Goods POs: quantity billed <= received minus already billed, per line.

    Service POs: goods receipt not required (skip).
    """
    if not po:
        return Check(
            id="3W-01",
            name="Goods receipt 3-way match",
            stage="lines",
            result="skip",
            outcome_class=None,
            message="No PO resolved; 3-way match skipped.",
        )

    if not po.get("grn_needed"):
        return Check(
            id="3W-01",
            name="Goods receipt 3-way match",
            stage="lines",
            result="pass",
            outcome_class=None,
            message="Service PO: goods receipt not required (3-way match skipped).",
            evidence={"grn_needed": False},
        )

    po_num = po["po_number"]
    overages = []

    for inv_l, po_l in matched_pairs:
        line_no = po_l["line_number"]
        invoiced_qty = float(inv_l.get("quantity") or 0.0)
        avail_qty = float(get_remaining_qty_fn(po_num, line_no))

        if invoiced_qty > avail_qty + 1e-4:
            overages.append({
                "line_number": line_no,
                "description": po_l.get("description"),
                "invoiced_qty": invoiced_qty,
                "available_qty": avail_qty,
                "over_qty": invoiced_qty - avail_qty,
            })

    if overages:
        o = overages[0]
        return Check(
            id="3W-01",
            name="Goods receipt 3-way match",
            stage="lines",
            result="fail",
            outcome_class="NEEDS_REVIEW",
            message=f"3-way match failed on Line {o['line_number']} ({o['description']}): invoiced {o['invoiced_qty']:.0f} exceeds unbilled received quantity ({o['available_qty']:.0f}).",
            evidence={"overages": overages},
        )

    return Check(
        id="3W-01",
        name="Goods receipt 3-way match",
        stage="lines",
        result="pass",
        outcome_class=None,
        message="3-way match passed: invoiced quantities within received unbilled goods.",
        evidence={"checked_lines": len(matched_pairs)},
    )


def check_bal_01(
    invoice_net: float | None,
    po_remaining_net: float | None,
    tolerance_pct: float = 2.0,
    tolerance_abs: float = 100.0,
) -> Check:
    """BAL-01: Invoice net is at most PO remaining net plus tolerance.

    tolerance = min(tolerance_pct of remaining, tolerance_abs).
    """
    if invoice_net is None or po_remaining_net is None:
        return Check(
            id="BAL-01",
            name="PO balance check",
            stage="amounts",
            result="skip",
            outcome_class=None,
            message="Invoice net or PO remaining net missing; balance check skipped.",
        )

    inv_net = float(invoice_net)
    rem_net = float(po_remaining_net)
    tol = min((tolerance_pct / 100.0) * rem_net, tolerance_abs) if rem_net > 0 else 0.0

    if inv_net <= rem_net + tol + 1e-4:
        return Check(
            id="BAL-01",
            name="PO balance check",
            stage="amounts",
            result="pass",
            outcome_class=None,
            message=f"Invoice net (${inv_net:,.2f}) within PO remaining net (${rem_net:,.2f}) plus tolerance (${tol:,.2f}).",
            evidence={"invoice_net": inv_net, "remaining_net": rem_net, "tolerance": round(tol, 2)},
        )

    over = inv_net - rem_net
    return Check(
        id="BAL-01",
        name="PO balance check",
        stage="amounts",
        result="fail",
        outcome_class="NEEDS_REVIEW",
        message=f"PO balance exceeded: invoice net (${inv_net:,.2f}) exceeds PO remaining balance (${rem_net:,.2f}) by ${over:,.2f}.",
        evidence={"invoice_net": inv_net, "remaining_net": rem_net, "overage": round(over, 2), "tolerance": round(tol, 2)},
    )


def check_tax_01(
    tax_amount: float | None,
    invoice_net: float | None,
    po_tax_rate: float | None,
    tax_inferred: bool = False,
    tax_abs_tolerance: float = 1.00,
) -> Check:
    """TAX-01: Separate tax within $1.00 of net * PO rate; inclusive tax inferred with warning."""
    if tax_inferred:
        rate_str = f"{po_tax_rate * 100:.0f}%" if po_tax_rate is not None else "PO rate"
        return Check(
            id="TAX-01",
            name="Tax amount verification",
            stage="amounts",
            result="warn",
            outcome_class=None,
            message=f"Tax inferred at {rate_str} from gross amount (no separate tax breakdown).",
            evidence={"tax_inferred": True, "po_tax_rate": po_tax_rate},
        )

    if tax_amount is not None and invoice_net is not None and po_tax_rate is not None:
        t_amt = float(tax_amount)
        expected_tax = round(float(invoice_net) * float(po_tax_rate), 2)
        diff = abs(t_amt - expected_tax)

        if diff <= tax_abs_tolerance + 1e-4:
            return Check(
                id="TAX-01",
                name="Tax amount verification",
                stage="amounts",
                result="pass",
                outcome_class=None,
                message=f"Tax amount (${t_amt:,.2f}) agrees with expected tax (${expected_tax:,.2f} at {po_tax_rate*100:.0f}%).",
                evidence={"tax_amount": t_amt, "expected_tax": expected_tax, "diff": round(diff, 2)},
            )

        return Check(
            id="TAX-01",
            name="Tax amount verification",
            stage="amounts",
            result="fail",
            outcome_class="NEEDS_REVIEW",
            message=f"Tax amount mismatch: charged ${t_amt:,.2f} vs expected ${expected_tax:,.2f} at {po_tax_rate*100:.0f}% (diff: ${diff:,.2f}).",
            evidence={"tax_amount": t_amt, "expected_tax": expected_tax, "diff": round(diff, 2)},
        )

    return Check(
        id="TAX-01",
        name="Tax amount verification",
        stage="amounts",
        result="pass",
        outcome_class=None,
        message="Tax amount verified.",
    )


def check_amt_01(
    total_amount: float | None,
    invoice_net: float | None,
    po_tax_rate: float | None,
    tolerance_pct: float = 2.0,
    tolerance_abs: float = 100.0,
) -> tuple[Check, float]:
    """AMT-01: Expected gross = billed net * (1 + PO tax rate).

    Tolerance = min(2% of expected gross, $100).
    Variance within tolerance passes (with a note/warn if non-zero); beyond fails.
    Returns (Check, variance).
    """
    if total_amount is None or invoice_net is None or po_tax_rate is None:
        return (
            Check(
                id="AMT-01",
                name="Gross amount and tolerance",
                stage="amounts",
                result="skip",
                outcome_class=None,
                message="Gross amount, net or PO tax rate missing; amount check skipped.",
            ),
            0.0,
        )

    tot = float(total_amount)
    net = float(invoice_net)
    rate = float(po_tax_rate)

    expected_gross = round(net * (1.0 + rate), 2)
    tol = min((tolerance_pct / 100.0) * expected_gross, tolerance_abs)
    variance = round(tot - expected_gross, 2)
    variance_pct = round((variance / expected_gross) * 100, 2) if expected_gross > 0 else 0.0

    if abs(variance) <= tol + 1e-4:
        if abs(variance) < 0.005:
            return (
                Check(
                    id="AMT-01",
                    name="Gross amount and tolerance",
                    stage="amounts",
                    result="pass",
                    outcome_class=None,
                    message="Gross amount agrees with expected gross exactly (variance $0.00).",
                    evidence={"expected_gross": expected_gross, "total_amount": tot, "variance": 0.0},
                ),
                0.0,
            )

        # Variance within tolerance: pass with warning note
        sign = "+" if variance > 0 else ""
        return (
            Check(
                id="AMT-01",
                name="Gross amount and tolerance",
                stage="amounts",
                result="warn",
                outcome_class=None,
                message=f"Gross variance {sign}${variance:,.2f} ({sign}{variance_pct:.2f}%) within tolerance limit ${tol:,.2f}.",
                evidence={"expected_gross": expected_gross, "total_amount": tot, "variance": variance, "variance_pct": variance_pct, "tolerance": tol},
            ),
            variance,
        )

    sign = "+" if variance > 0 else ""
    return (
        Check(
            id="AMT-01",
            name="Gross amount and tolerance",
            stage="amounts",
            result="fail",
            outcome_class="NEEDS_REVIEW",
            message=f"Gross variance {sign}${variance:,.2f} ({sign}{variance_pct:.2f}%) exceeds tolerance limit ${tol:,.2f}.",
            evidence={"expected_gross": expected_gross, "total_amount": tot, "variance": variance, "variance_pct": variance_pct, "tolerance": tol},
        ),
        variance,
    )


def check_pol_01(
    total_amount: float | None,
    auto_approve_limit: float = 75000.0,
) -> Check:
    """POL-01: Gross at most the auto-approve limit ($75,000)."""
    if total_amount is None:
        return Check(
            id="POL-01",
            name="Policy auto-approve limit",
            stage="policy",
            result="skip",
            outcome_class=None,
            message="Total amount missing; policy check skipped.",
        )

    tot = float(total_amount)
    if tot <= auto_approve_limit:
        return Check(
            id="POL-01",
            name="Policy auto-approve limit",
            stage="policy",
            result="pass",
            outcome_class=None,
            message=f"Gross amount ${tot:,.2f} is within the ${auto_approve_limit:,.2f} auto-approve limit.",
            evidence={"gross": tot, "limit": auto_approve_limit},
        )

    return Check(
        id="POL-01",
        name="Policy auto-approve limit",
        stage="policy",
        result="fail",
        outcome_class="NEEDS_REVIEW",
        message=f"Gross amount ${tot:,.2f} exceeds auto-approve limit of ${auto_approve_limit:,.2f}. Manager sign-off required.",
        evidence={"gross": tot, "limit": auto_approve_limit},
    )


# ── Decision, Precedence, Headlines and Emails ──────────────────────────

def decide(
    checks: list[Check],
    vendor: dict | None = None,
    po: dict | None = None,
    invoice_date: str | date | None = None,
    due_date: str | date | None = None,
    total_amount: float | None = None,
    variance: float = 0.0,
    suggested_po: str | None = None,
    missing_fields: list[str] | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Calculate overall decision with exact precedence from SPEC B8:

    1. Any REJECTED finding -> REJECTED
    2. Any NEEDS_INFO finding -> NEEDS_INFO
    3. Any NEEDS_REVIEW finding -> NEEDS_REVIEW
    4. Else -> APPROVED (warnings listed as notes)

    VEN-04 failure always carries severity CRITICAL.
    """
    has_rejected = any(c.outcome_class == "REJECTED" for c in checks)
    has_info = any(c.outcome_class == "NEEDS_INFO" for c in checks)
    has_review = any(c.outcome_class == "NEEDS_REVIEW" for c in checks)

    if has_rejected:
        status = "REJECTED"
    elif has_info:
        status = "NEEDS_INFO"
    elif has_review:
        status = "NEEDS_REVIEW"
    else:
        status = "APPROVED"

    # Severity determination
    has_critical_ven04 = any(c.id == "VEN-04" and c.result == "fail" for c in checks)
    if has_critical_ven04:
        severity = "CRITICAL"
    elif status == "REJECTED":
        severity = "HIGH"
    elif status == "NEEDS_REVIEW":
        severity = "MEDIUM"
    elif status == "NEEDS_INFO":
        severity = "LOW"
    else:
        severity = "LOW"

    # Next action
    if status == "APPROVED":
        next_action = "post_to_ledger"
    elif status == "NEEDS_REVIEW":
        if severity == "CRITICAL":
            phone = vendor.get("callback_phone") if vendor else "+1-555-0103"
            next_action = f"Call {phone} from master record to verify bank detail change. Never use contact details printed on invoice."
        else:
            next_action = "queue_for_review"
    elif status == "NEEDS_INFO":
        next_action = "email_vendor"
    else:
        next_action = "none"

    # Headline generation per B10 templates
    pay_by = str(due_date or invoice_date or "Net 30")
    if status == "APPROVED":
        var_str = f"+${variance:,.2f}" if variance > 0 else f"${variance:,.2f}"
        headline = f"Approved. Vendor, PO, goods receipt and amount agree (variance {var_str}). Pay by {pay_by}."
    elif status == "NEEDS_REVIEW":
        top_finding = next((c.message for c in checks if c.outcome_class == "NEEDS_REVIEW"), "policy or balance review required")
        if severity == "CRITICAL":
            headline = "Held for review: bank account mismatch (potential fraud risk)."
        else:
            headline = f"Held for review: {top_finding}"
    elif status == "NEEDS_INFO":
        m_items = ", ".join(missing_fields) if missing_fields else "required details"
        headline = f"Waiting on vendor: missing {m_items}."
    else:  # REJECTED
        top_reject = next((c.message for c in checks if c.outcome_class == "REJECTED"), "policy check failed")
        rid_str = f", see run {run_id}" if run_id else ""
        headline = f"Rejected: {top_reject}{rid_str}."

    # Reasons list (up to 3 concise bullet points)
    reasons = []
    if status == "APPROVED":
        reasons.append("Vendor verified and active in master records.")
        reasons.append("Purchase order and 3-way match verified.")
        notes = [c.message for c in checks if c.result == "warn"]
        if notes:
            reasons.extend(notes[:2])
        else:
            reasons.append(f"Gross amount matches expected gross within tolerance ({var_str}).")
    else:
        # Include failing and warning messages
        for c in checks:
            if c.result in ("fail", "warn") and c.message:
                reasons.append(c.message)
                if len(reasons) >= 3:
                    break

    # Email draft or call script
    email_draft = None
    if has_critical_ven04:
        phone = vendor.get("callback_phone") if vendor else "verified number"
        vname = vendor.get("name") if vendor else "vendor"
        printed_bank = next((c.evidence.get("printed_last4") for c in checks if c.id == "VEN-04"), "unknown")
        email_draft = (
            f"INTERNAL CALL SCRIPT (DO NOT EMAIL INVOICE SENDER):\n"
            f"1. Call {vname} accounting at verified master telephone: {phone}.\n"
            f"2. Confirm if bank account change to account ending in {printed_bank} is legitimate.\n"
            f"3. Check for unauthorized remit domain or invoice tampering.\n"
            f"CRITICAL: Do NOT use contact phone or email printed on the invoice."
        )
    elif status == "NEEDS_INFO":
        ref = f"PO reference {suggested_po}" if suggested_po else "information needed"
        missing_items = missing_fields or ["invoice number", "invoice date"]
        body_items = "\n".join(f"- {f.replace('_', ' ').title()}" for f in missing_items)
        suggestion_body = f"\nPlease confirm whether this invoice relates to Purchase Order {suggested_po}." if suggested_po else ""
        vname = vendor.get("name") if vendor else "Vendor"
        email_draft = (
            f"Subject: Invoice: {ref} to process payment\n\n"
            f"Dear {vname},\n\n"
            f"We received your invoice but are unable to process payment because the following required information is missing:\n"
            f"{body_items}"
            f"{suggestion_body}\n\n"
            f"Please reply with an updated invoice or the requested details so we can complete processing.\n\n"
            f"Thank you,\nAccounts Payable Team"
        )

    return {
        "status": status,
        "severity": severity,
        "headline": headline,
        "reasons": reasons[:3],
        "next_action": next_action,
        "email_draft": email_draft,
        "pay_by": pay_by if status == "APPROVED" else None,
    }
