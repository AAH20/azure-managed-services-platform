"""Local-first managed backup service delivery and draft economics."""

from __future__ import annotations

import json
import secrets
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from .backup_resolution import validate_scope
from .partner_cloud import MONTH, _cents, _id, _text, _usd, authenticate
from .resolution_network import get_case
from .resolution_network import initialize as initialize_cases


class DeliveryError(ValueError):
    """Invalid pilot, scope, evidence, or accounting input."""


RESOURCE_KEYS = ("tenant_id", "subscription_id", "resource_group", "vault_name", "vm_name")


def initialize(db: Path) -> None:
    initialize_cases(db)
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("""CREATE TABLE IF NOT EXISTS pilot_requests (
            request_id TEXT PRIMARY KEY, partner_id TEXT NOT NULL, customer_id TEXT NOT NULL,
            requested_vm_count INTEGER NOT NULL, state TEXT NOT NULL, created_at TEXT NOT NULL,
            FOREIGN KEY(partner_id) REFERENCES partners(partner_id))""")
        conn.execute("""CREATE TABLE IF NOT EXISTS delivery_pilots (
            pilot_id TEXT PRIMARY KEY, request_id TEXT NOT NULL UNIQUE,
            partner_id TEXT NOT NULL, customer_id TEXT NOT NULL, scope_json TEXT NOT NULL,
            contract_ref TEXT NOT NULL, monthly_fee_cents INTEGER NOT NULL,
            hourly_cost_cents INTEGER NOT NULL, customer_token_hash TEXT NOT NULL,
            state TEXT NOT NULL, onboarded_at TEXT NOT NULL, active_at TEXT,
            activation_evidence_sha256 TEXT,
            FOREIGN KEY(request_id) REFERENCES pilot_requests(request_id))""")
        conn.execute("""CREATE TABLE IF NOT EXISTS delivery_work (
            work_id TEXT PRIMARY KEY, pilot_id TEXT NOT NULL, case_id TEXT NOT NULL,
            minutes INTEGER NOT NULL, note TEXT NOT NULL, recorded_at TEXT NOT NULL,
            FOREIGN KEY(pilot_id) REFERENCES delivery_pilots(pilot_id))""")


def _row(row: sqlite3.Row) -> dict:
    result = dict(row)
    if "scope_json" in result:
        result["scope"] = json.loads(result.pop("scope_json"))
    result.pop("customer_token_hash", None)
    for key in ("monthly_fee_cents", "hourly_cost_cents"):
        if key in result:
            result[key.removesuffix("_cents") + "_usd"] = _usd(result.pop(key))
    return result


def _pilot(db: Path, pilot_id: str) -> dict:
    with closing(sqlite3.connect(db)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM delivery_pilots WHERE pilot_id=?", (pilot_id,)).fetchone()
    if row is None:
        raise DeliveryError("pilot not found")
    return _row(row)


def request_pilot(db: Path, partner_id: str, token: str, customer_id: str,
                  requested_vm_count: int) -> dict:
    _id(customer_id, "customer_id")
    if not isinstance(requested_vm_count, int) or isinstance(requested_vm_count, bool) or not 1 <= requested_vm_count <= 100:
        raise DeliveryError("requested_vm_count must be an integer from 1 to 100")
    if not authenticate(db, partner_id, token):
        raise DeliveryError("partner authentication failed")
    initialize(db)
    record = {"request_id": "req-" + uuid4().hex[:20], "partner_id": partner_id,
              "customer_id": customer_id, "requested_vm_count": requested_vm_count,
              "state": "requested", "created_at": datetime.now(UTC).isoformat()}
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("INSERT INTO pilot_requests VALUES (?,?,?,?,?,?)", tuple(record.values()))
    return record


def list_requests(db: Path, partner_id: str, token: str) -> list[dict]:
    if not authenticate(db, partner_id, token):
        raise DeliveryError("partner authentication failed")
    initialize(db)
    with closing(sqlite3.connect(db)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM pilot_requests WHERE partner_id=? ORDER BY created_at",
                            (partner_id,)).fetchall()
    return [dict(row) for row in rows]


def onboard(db: Path, request_id: str, scope: dict, contract_ref: str,
            monthly_fee_usd: str, hourly_cost_usd: str, customer_token: str) -> dict:
    scope = validate_scope(scope)
    contract_ref = _text(contract_ref, "contract_ref")
    if not isinstance(customer_token, str) or len(customer_token) < 32:
        raise DeliveryError("customer token must contain at least 32 characters")
    monthly = _cents(monthly_fee_usd, "monthly_fee_usd")
    hourly = _cents(hourly_cost_usd, "hourly_cost_usd")
    if monthly == 0:
        raise DeliveryError("monthly fee must be positive")
    initialize(db)
    pilot_id = "pilot-" + uuid4().hex[:20]
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM pilot_requests WHERE request_id=? AND state='requested'",
                           (request_id,)).fetchone()
        if row is None:
            raise DeliveryError("request not found or already onboarded")
        if row["requested_vm_count"] != 1:
            raise DeliveryError("first release supports exactly one VM per pilot")
        conn.execute("""INSERT INTO delivery_pilots VALUES
            (?,?,?,?,?,?,?,?,?,'onboarded',?,NULL,NULL)""",
            (pilot_id, request_id, row["partner_id"], row["customer_id"],
             json.dumps(scope, sort_keys=True), contract_ref, monthly, hourly,
             sha256(customer_token.encode()).hexdigest(), datetime.now(UTC).isoformat()))
        conn.execute("UPDATE pilot_requests SET state='onboarded' WHERE request_id=?", (request_id,))
    return _pilot(db, pilot_id)


def activate(db: Path, pilot_id: str, snapshot: dict) -> dict:
    pilot = _pilot(db, pilot_id)
    if pilot["state"] != "onboarded":
        raise DeliveryError("pilot is not awaiting activation")
    if not isinstance(snapshot, dict) or snapshot.get("scope") != pilot["scope"]:
        raise DeliveryError("activation snapshot scope mismatch")
    if (snapshot.get("source_status") != "observed" or snapshot.get("account_verified") is not True
            or not isinstance(snapshot.get("item"), dict)):
        raise DeliveryError("observed account-verified backup evidence is required")
    receipts = snapshot.get("receipts")
    if (not isinstance(receipts, list) or len(receipts) != 4
            or any(not isinstance(item, dict) or item.get("status") != "observed"
                   or item.get("tenant_id") != pilot["scope"]["tenant_id"]
                   or item.get("subscription_id") != pilot["scope"]["subscription_id"]
                   for item in receipts)):
        raise DeliveryError("backup evidence receipts are required")
    digest = sha256(json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    with closing(sqlite3.connect(db)) as conn, conn:
        updated = conn.execute("""UPDATE delivery_pilots SET state='active', active_at=?,
            activation_evidence_sha256=? WHERE pilot_id=? AND state='onboarded'""",
            (datetime.now(UTC).isoformat(), digest, pilot_id)).rowcount
        if updated != 1:
            raise DeliveryError("pilot activation raced another update")
    return _pilot(db, pilot_id)


def _matches(pilot: dict, case: dict) -> bool:
    return all(pilot["scope"][key] == case["scope"][key] for key in RESOURCE_KEYS)


def record_work(db: Path, pilot_id: str, case_id: str, minutes: int, note: str) -> dict:
    pilot = _pilot(db, pilot_id)
    if pilot["state"] != "active":
        raise DeliveryError("pilot is not active")
    case = get_case(db, case_id)
    if not _matches(pilot, case):
        raise DeliveryError("case is outside pilot scope")
    if not isinstance(minutes, int) or isinstance(minutes, bool) or not 1 <= minutes <= 1440:
        raise DeliveryError("minutes must be an integer from 1 to 1440")
    note = _text(note, "note")
    record = {"work_id": "work-" + uuid4().hex[:20], "pilot_id": pilot_id,
              "case_id": case_id, "minutes": minutes, "note": note,
              "recorded_at": datetime.now(UTC).isoformat()}
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("INSERT INTO delivery_work VALUES (?,?,?,?,?,?)", tuple(record.values()))
    return record


def customer_authenticate(db: Path, pilot_id: str, token: str) -> bool:
    if not db.exists() or not isinstance(token, str):
        return False
    with closing(sqlite3.connect(db)) as conn:
        row = conn.execute("SELECT customer_token_hash FROM delivery_pilots WHERE pilot_id=?",
                           (pilot_id,)).fetchone()
    return bool(row and secrets.compare_digest(row[0], sha256(token.encode()).hexdigest()))


def _report(db: Path, pilot_id: str, month: str) -> dict:
    if not isinstance(month, str) or not MONTH.fullmatch(month):
        raise DeliveryError("month must be YYYY-MM")
    pilot = _pilot(db, pilot_id)
    with closing(sqlite3.connect(db)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT case_id,state,created_at,scope_json,outcome_json FROM cases "
                            "WHERE created_at >= ? AND created_at < ? ORDER BY created_at",
                            (month + "-01", _next_month(month))).fetchall()
        work = conn.execute("SELECT minutes FROM delivery_work WHERE pilot_id=? AND "
                            "recorded_at >= ? AND recorded_at < ?",
                            (pilot_id, month + "-01", _next_month(month))).fetchall()
    cases = []
    for row in rows:
        if all(pilot["scope"][key] == json.loads(row["scope_json"])[key] for key in RESOURCE_KEYS):
            outcome = json.loads(row["outcome_json"]) if row["outcome_json"] else None
            cases.append({"case_id": row["case_id"], "state": row["state"],
                          "created_at": row["created_at"],
                          "backup_result": outcome.get("status") if outcome else None})
    minutes = sum(row["minutes"] for row in work)
    hourly = _cents(pilot["hourly_cost_usd"], "hourly_cost_usd")
    labor = int((Decimal(minutes * hourly) / 60).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    active = pilot["active_at"] is not None and pilot["active_at"][:7] <= month
    fee = _cents(pilot["monthly_fee_usd"], "monthly_fee_usd") if active else 0
    return {"pilot_id": pilot_id, "customer_id": pilot["customer_id"], "month": month,
            "currency": "USD", "state": pilot["state"], "cases": cases,
            "case_count": len(cases), "operator_minutes": minutes,
            "estimated_operator_labor_usd": _usd(labor),
            "draft_customer_charge_usd": _usd(fee),
            "estimated_contribution_after_operator_labor_usd": _usd(fee - labor),
            "boundary": "Draft fixed-fee estimate only; excludes cloud costs, overhead, tax, credits, proration, invoice and payment. Case status does not prove a successful restore."}


def customer_report(db: Path, pilot_id: str, token: str, month: str) -> dict:
    if not customer_authenticate(db, pilot_id, token):
        raise DeliveryError("customer authentication failed")
    result = _report(db, pilot_id, month)
    result.pop("estimated_operator_labor_usd")
    result.pop("estimated_contribution_after_operator_labor_usd")
    result.pop("operator_minutes")
    return result


def operator_statement(db: Path, pilot_id: str, month: str) -> dict:
    """Local operator-only view; the CLI must remain on a trusted machine."""
    return _report(db, pilot_id, month)


def _next_month(month: str) -> str:
    year, number = map(int, month.split("-"))
    return f"{year + (number == 12):04d}-{number % 12 + 1:02d}-01"
