"""Single-SKU partner ordering and draft monthly statements."""

from __future__ import annotations

import json
import re
import secrets
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from .managed_ai import monthly_report, validate_service

IDENTIFIER = re.compile(r"^[a-z][a-z0-9-]{1,62}$")
MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


class PartnerError(ValueError):
    pass


def _id(value: object, label: str) -> str:
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise PartnerError(f"{label} must be a lowercase identifier")
    return value


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 160:
        raise PartnerError(f"{label} is required")
    return value.strip()


def _cents(value: object, label: str) -> int:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError):
        raise PartnerError(f"{label} must be money") from None
    if not amount.is_finite() or amount < 0 or amount.as_tuple().exponent < -2:
        raise PartnerError(f"{label} must be nonnegative USD with at most two decimals")
    return int(amount * 100)


def _usd(cents: int) -> str:
    return str((Decimal(cents) / 100).quantize(Decimal("0.01")))


def validate_catalog(value: dict) -> dict:
    if not isinstance(value, dict):
        raise PartnerError("catalog must be an object")
    sku = _id(value.get("sku"), "sku")
    model = _text(value.get("model"), "model")
    owner = _text(value.get("operator_owner"), "operator_owner")
    regions = value.get("allowed_regions")
    if not isinstance(regions, list) or not regions or any(not isinstance(r, str) for r in regions):
        raise PartnerError("allowed_regions must be a nonempty list")
    prices = {key: _cents(value.get(key), key) for key in (
        "retail_setup_usd", "wholesale_setup_usd", "retail_monthly_usd", "wholesale_monthly_usd"
    )}
    if prices["wholesale_setup_usd"] > prices["retail_setup_usd"] or prices["wholesale_monthly_usd"] > prices["retail_monthly_usd"]:
        raise PartnerError("wholesale prices cannot exceed retail prices")
    targets = value.get("service_targets")
    target_keys = {"minimum_samples_per_month", "minimum_sample_success_pct",
                   "maximum_p95_latency_ms", "maximum_p95_ttft_ms"}
    if not isinstance(targets, dict) or set(targets) != target_keys:
        raise PartnerError("service_targets must contain exactly the four probe target fields")
    try:
        validate_service({"service_id": "catalog-check", "customer": "catalog-check",
                          "base_url": "https://example.invalid", "model": model,
                          "owner": owner, **targets})
    except ValueError as exc:
        raise PartnerError(f"invalid service_targets: {exc}") from exc
    return {**value, "sku": sku, "model": model, "operator_owner": owner,
            "allowed_regions": regions, "prices_cents": prices}


def initialize(db: Path) -> None:
    db.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS partners (
            partner_id TEXT PRIMARY KEY, token_hash TEXT NOT NULL
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS partner_orders (
            order_id TEXT PRIMARY KEY, partner_id TEXT NOT NULL, customer_id TEXT NOT NULL,
            sku TEXT NOT NULL, region TEXT NOT NULL, model TEXT NOT NULL,
            state TEXT NOT NULL, requested_at TEXT NOT NULL, active_at TEXT,
            deployment_ref TEXT, contract_ref TEXT, endpoint_url TEXT,
            operator_owner TEXT NOT NULL, prices_json TEXT NOT NULL,
            service_targets_json TEXT NOT NULL,
            FOREIGN KEY(partner_id) REFERENCES partners(partner_id)
        )""")


def register_partner(db: Path, partner_id: str, token: str) -> None:
    _id(partner_id, "partner_id")
    if len(token) < 32:
        raise PartnerError("partner token must contain at least 32 characters")
    initialize(db)
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("INSERT INTO partners VALUES (?, ?)",
                     (partner_id, sha256(token.encode()).hexdigest()))


def authenticate(db: Path, partner_id: str, token: str) -> bool:
    if not db.exists() or not isinstance(token, str):
        return False
    with closing(sqlite3.connect(db)) as conn:
        row = conn.execute("SELECT token_hash FROM partners WHERE partner_id = ?", (partner_id,)).fetchone()
    return bool(row and secrets.compare_digest(row[0], sha256(token.encode()).hexdigest()))


def create_order(db: Path, catalog: dict, partner_id: str, request: dict) -> dict:
    catalog = validate_catalog(catalog)
    if not isinstance(request, dict):
        raise PartnerError("order body must be an object")
    customer_id = _id(request.get("customer_id"), "customer_id")
    region = _text(request.get("region"), "region")
    if region not in catalog["allowed_regions"]:
        raise PartnerError("region is not offered by this SKU")
    if request.get("sku") != catalog["sku"]:
        raise PartnerError("unknown SKU")
    order_id = "ord-" + uuid4().hex[:20]
    now = datetime.now(UTC).isoformat()
    initialize(db)
    with closing(sqlite3.connect(db)) as conn, conn:
        if not conn.execute("SELECT 1 FROM partners WHERE partner_id = ?", (partner_id,)).fetchone():
            raise PartnerError("partner is not registered")
        conn.execute("""INSERT INTO partner_orders VALUES
            (?, ?, ?, ?, ?, ?, 'requested', ?, NULL, NULL, NULL, NULL, ?, ?, ?)""",
            (order_id, partner_id, customer_id, catalog["sku"], region, catalog["model"], now,
             catalog["operator_owner"], json.dumps(catalog["prices_cents"], sort_keys=True),
             json.dumps(catalog["service_targets"], sort_keys=True)))
    return get_order(db, partner_id, order_id)


def _row_to_order(row: sqlite3.Row) -> dict:
    data = dict(row)
    prices = json.loads(data.pop("prices_json"))
    data["prices_usd"] = {key: _usd(value) for key, value in prices.items()}
    data["service_targets"] = json.loads(data.pop("service_targets_json"))
    return data


def get_order(db: Path, partner_id: str, order_id: str) -> dict | None:
    if not db.exists():
        return None
    with closing(sqlite3.connect(db)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM partner_orders WHERE partner_id = ? AND order_id = ?",
                           (partner_id, order_id)).fetchone()
    return _row_to_order(row) if row else None


def list_orders(db: Path, partner_id: str) -> list[dict]:
    if not db.exists():
        return []
    with closing(sqlite3.connect(db)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM partner_orders WHERE partner_id = ? ORDER BY requested_at",
                            (partner_id,)).fetchall()
    return [_row_to_order(row) for row in rows]


def activate_order(db: Path, order_id: str, deployment_ref: str, contract_ref: str,
                   endpoint_url: str) -> dict:
    _text(deployment_ref, "deployment_ref")
    _text(contract_ref, "contract_ref")
    _text(endpoint_url, "endpoint_url")
    if not endpoint_url.startswith("https://"):
        raise PartnerError("active endpoint must use HTTPS")
    parsed = urlsplit(endpoint_url)
    if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/"):
        raise PartnerError("active endpoint must be a bare HTTPS origin without credentials")
    now = datetime.now(UTC).isoformat()
    with closing(sqlite3.connect(db)) as conn, conn:
        updated = conn.execute("""UPDATE partner_orders SET state = 'active', active_at = ?,
            deployment_ref = ?, contract_ref = ?, endpoint_url = ?
            WHERE order_id = ? AND state = 'requested'""",
            (now, deployment_ref, contract_ref, endpoint_url, order_id)).rowcount
        if updated != 1:
            raise PartnerError("order not found or not awaiting activation")
        partner_id = conn.execute("SELECT partner_id FROM partner_orders WHERE order_id = ?",
                                  (order_id,)).fetchone()[0]
    return get_order(db, partner_id, order_id)


def statement(db: Path, catalog: dict, partner_id: str, order_id: str, month: str) -> dict:
    catalog = validate_catalog(catalog)
    if not MONTH.fullmatch(month):
        raise PartnerError("month must be YYYY-MM")
    order = get_order(db, partner_id, order_id)
    if order is None:
        raise PartnerError("order not found")
    active_month = order["active_at"][:7] if order["active_at"] else None
    active = order["state"] == "active" and active_month <= month
    prices = {key: _cents(value, key) for key, value in order["prices_usd"].items()}
    setup = active and active_month == month
    retail = (prices["retail_monthly_usd"] + (prices["retail_setup_usd"] if setup else 0)) if active else 0
    wholesale = (prices["wholesale_monthly_usd"] + (prices["wholesale_setup_usd"] if setup else 0)) if active else 0
    status = None
    if active:
        status = monthly_report(db, service_definition(order), month)
    return {
        "order_id": order_id, "partner_id": partner_id, "customer_id": order["customer_id"],
        "month": month, "currency": "USD", "state": order["state"],
        "draft_customer_charge_usd": _usd(retail),
        "draft_partner_wholesale_usd": _usd(wholesale),
        "draft_partner_gross_spread_usd": _usd(retail - wholesale),
        "service_probe_report": status,
        "boundary": "Draft contract-price statement only; no invoice, payment, customer usage meter, tax or proration. Probe samples are operational checks, not billable usage.",
    }


def service_definition(order: dict) -> dict:
    if order["state"] != "active":
        raise PartnerError("order is not active")
    return {
        "service_id": order["order_id"], "customer": order["customer_id"],
        "base_url": order["endpoint_url"], "model": order["model"],
        "owner": order["operator_owner"], **order["service_targets"],
    }
