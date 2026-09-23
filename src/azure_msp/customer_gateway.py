"""Single-customer authenticated alert inbox and durable local worker."""

from __future__ import annotations

import hmac
import json
import re
import secrets
import sqlite3
import time
from contextlib import closing
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from .backup_resolution import validate_scope
from .production_resolution import open_case
from .resolution_network import parse_alert, validate_recipe

NONCE = re.compile(r"^[A-Za-z0-9._-]{16,128}$")
SIGNATURE = re.compile(r"^[0-9a-f]{64}$")
MAX_BODY = 65536
MAX_SKEW_SECONDS = 300
MAX_ATTEMPTS = 5


class GatewayError(ValueError):
    """Rejected alert, authentication, replay, or queue transition."""


def validate_config(config: dict) -> dict:
    if not isinstance(config, dict) or set(config) != {"customer_id", "scope", "recipe"}:
        raise GatewayError("config must contain customer_id, scope and recipe")
    customer = config["customer_id"]
    if not isinstance(customer, str) or not re.fullmatch(r"[a-z][a-z0-9-]{1,62}", customer):
        raise GatewayError("customer_id must be a lowercase identifier")
    return {"customer_id": customer, "scope": validate_scope(config["scope"]),
            "recipe": validate_recipe(config["recipe"])}


def initialize(db: Path) -> None:
    db.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""CREATE TABLE IF NOT EXISTS gateway_nonces (
            nonce TEXT PRIMARY KEY, seen_at INTEGER NOT NULL)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS gateway_identity (
            id INTEGER PRIMARY KEY CHECK(id=1), config_sha256 TEXT NOT NULL)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS gateway_messages (
            message_id TEXT PRIMARY KEY, alert_id TEXT NOT NULL UNIQUE,
            payload_sha256 TEXT NOT NULL, body_json TEXT NOT NULL,
            state TEXT NOT NULL, attempts INTEGER NOT NULL, lease_until INTEGER,
            case_id TEXT, error_class TEXT, received_at TEXT NOT NULL)""")


def _bind_config(db: Path, config: dict) -> None:
    digest = sha256(json.dumps(config, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT config_sha256 FROM gateway_identity WHERE id=1").fetchone()
        if row is None:
            conn.execute("INSERT INTO gateway_identity VALUES (1,?)", (digest,))
        elif row[0] != digest:
            raise GatewayError("database is bound to a different customer gateway config")


def sign_payload(key: str, timestamp: int, nonce: str, body: bytes) -> str:
    if not isinstance(key, str) or len(key) < 32:
        raise GatewayError("HMAC key must contain at least 32 characters")
    if not isinstance(timestamp, int) or not isinstance(nonce, str) or not NONCE.fullmatch(nonce):
        raise GatewayError("invalid timestamp or nonce")
    return hmac.new(key.encode(), f"{timestamp}.{nonce}.".encode() + body, sha256).hexdigest()


def enqueue(db: Path, config: dict, body: bytes, *, timestamp: int, nonce: str,
            signature: str, key: str, now: int | None = None) -> dict:
    config = validate_config(config)
    if not isinstance(body, bytes) or not 0 < len(body) <= MAX_BODY:
        raise GatewayError("alert body must be 1..65536 bytes")
    current = int(time.time()) if now is None else now
    if type(timestamp) is not int or abs(current - timestamp) > MAX_SKEW_SECONDS:
        raise GatewayError("alert timestamp is outside the five-minute window")
    if (not isinstance(signature, str) or not SIGNATURE.fullmatch(signature)
            or not secrets.compare_digest(signature, sign_payload(key, timestamp, nonce, body))):
        raise GatewayError("invalid alert signature")
    try:
        alert = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise GatewayError("alert body is not JSON") from None
    normalized = parse_alert(alert, config["scope"], config["recipe"])
    digest = sha256(body).hexdigest()
    initialize(db)
    _bind_config(db, config)
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM gateway_nonces WHERE seen_at < ?", (current - 600,))
        try:
            conn.execute("INSERT INTO gateway_nonces VALUES (?,?)", (nonce, current))
        except sqlite3.IntegrityError:
            raise GatewayError("replayed alert nonce") from None
        row = conn.execute("SELECT message_id,payload_sha256,state,case_id FROM gateway_messages "
                           "WHERE alert_id=?", (normalized["alert_id"],)).fetchone()
        if row:
            if row[1] != digest:
                raise GatewayError("alert ID has a conflicting payload")
            return {"message_id": row[0], "state": row[2], "case_id": row[3], "duplicate": True}
        message_id = "msg-" + uuid4().hex[:20]
        conn.execute("""INSERT INTO gateway_messages VALUES
            (?,?,?,?,'pending',0,NULL,NULL,NULL,?)""",
            (message_id, normalized["alert_id"], digest, body.decode(),
             datetime.now(UTC).isoformat()))
    return {"message_id": message_id, "state": "pending", "case_id": None, "duplicate": False}


def work_once(db: Path, config: dict, *, now: int | None = None) -> dict | None:
    config = validate_config(config)
    initialize(db)
    _bind_config(db, config)
    current = int(time.time()) if now is None else now
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("""SELECT message_id,body_json,attempts FROM gateway_messages
            WHERE state='pending' OR (state='leased' AND lease_until <= ?)
            ORDER BY received_at LIMIT 1""", (current,)).fetchone()
        if row is None:
            return None
        message_id, body, attempts = row
        if attempts >= MAX_ATTEMPTS:
            conn.execute("UPDATE gateway_messages SET state='dead',lease_until=NULL WHERE message_id=?",
                         (message_id,))
            return {"message_id": message_id, "state": "dead", "case_id": None}
        conn.execute("""UPDATE gateway_messages SET state='leased',attempts=attempts+1,
            lease_until=? WHERE message_id=?""", (current + 60, message_id))
    try:
        case = open_case(db, config["recipe"], json.loads(body), config["scope"])
    except Exception as exc:  # noqa: BLE001 - persist a failed lease for any worker exception
        with closing(sqlite3.connect(db)) as conn, conn:
            conn.execute("""UPDATE gateway_messages SET state=?,lease_until=NULL,error_class=?
                WHERE message_id=?""", ("dead" if attempts + 1 >= MAX_ATTEMPTS else "pending",
                                         type(exc).__name__, message_id))
        return {"message_id": message_id, "state": "dead" if attempts + 1 >= MAX_ATTEMPTS else "pending",
                "case_id": None, "error_class": type(exc).__name__}
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("""UPDATE gateway_messages SET state='complete',lease_until=NULL,
            case_id=?,error_class=NULL WHERE message_id=?""", (case["case_id"], message_id))
    return {"message_id": message_id, "state": "complete", "case_id": case["case_id"]}


def queue_status(db: Path, config: dict) -> dict:
    config = validate_config(config)
    initialize(db)
    _bind_config(db, config)
    with closing(sqlite3.connect(db)) as conn:
        rows = conn.execute("SELECT state,COUNT(*) FROM gateway_messages GROUP BY state").fetchall()
    return {"counts": dict(rows), "boundary": "Counts cover the local HMAC inbox only; Service Bus cases use the case ledger. No hosted ingress is active."}
