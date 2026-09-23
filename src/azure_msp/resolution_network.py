"""Local durable case ledger for a versioned operational workcell."""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from .backup_resolution import propose, validate_scope, verify

VERSION = re.compile(r"^\d+\.\d+\.\d+$")
BACKUP_WORKCELL = "azure-vm-backup-resolution"


class NetworkError(ValueError):
    """Invalid recipe, alert, case transition or case history."""


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def validate_recipe(value: dict) -> dict:
    if not isinstance(value, dict):
        raise NetworkError("recipe must be an object")
    if value.get("workcell_id") != BACKUP_WORKCELL or value.get("adapter") != "azure_vm_backup_v1":
        raise NetworkError("unsupported workcell adapter")
    if not isinstance(value.get("version"), str) or not VERSION.fullmatch(value["version"]):
        raise NetworkError("recipe version must be MAJOR.MINOR.PATCH")
    if value.get("alert_schema") != "azureMonitorCommonAlertSchema":
        raise NetworkError("unsupported alert schema")
    if value.get("target_resource_type") != "Microsoft.RecoveryServices/vaults":
        raise NetworkError("unsupported alert target resource type")
    return value


def parse_alert(alert: dict, scope: dict, recipe: dict) -> dict:
    scope = validate_scope(scope)
    validate_recipe(recipe)
    if not isinstance(alert, dict) or alert.get("schemaId") != recipe["alert_schema"]:
        raise NetworkError("alert is not Azure Monitor common alert schema")
    data = alert.get("data")
    if not isinstance(data, dict):
        raise NetworkError("alert data must be an object")
    essentials = data.get("essentials")
    if not isinstance(essentials, dict) or essentials.get("monitorCondition") != "Fired":
        raise NetworkError("alert must be Fired")
    alert_id = essentials.get("alertId")
    if not isinstance(alert_id, str) or not alert_id.strip() or len(alert_id) > 512:
        raise NetworkError("alertId is required")
    fired_at = essentials.get("firedDateTime")
    try:
        when = datetime.fromisoformat(fired_at)
        if when.tzinfo is None:
            raise ValueError
    except (TypeError, ValueError):
        raise NetworkError("firedDateTime must include a timezone") from None
    targets = essentials.get("alertTargetIDs")
    if not isinstance(targets, list) or not targets or any(not isinstance(x, str) for x in targets):
        raise NetworkError("alertTargetIDs must be a nonempty list")
    vault = (f"/subscriptions/{scope['subscription_id']}/resourceGroups/{scope['resource_group']}"
             f"/providers/Microsoft.RecoveryServices/vaults/{scope['vault_name']}").lower()
    if not any(target.lower() == vault or target.lower().startswith(vault + "/") for target in targets):
        raise NetworkError("alert target is outside the declared vault scope")
    return {"alert_id": alert_id, "fired_at": when.astimezone(UTC).isoformat(),
            "target_vault": vault, "severity": essentials.get("severity")}


def initialize(db: Path) -> None:
    db.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS cases (
            case_id TEXT PRIMARY KEY, workcell_id TEXT NOT NULL, version TEXT NOT NULL,
            alert_id TEXT NOT NULL, scope_json TEXT NOT NULL, state TEXT NOT NULL,
            created_at TEXT NOT NULL, snapshot_json TEXT, plan_json TEXT,
            attempt_json TEXT, outcome_json TEXT, after_json TEXT,
            UNIQUE(workcell_id, alert_id))""")
        conn.execute("""CREATE TABLE IF NOT EXISTS case_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT, case_id TEXT NOT NULL,
            event_json TEXT NOT NULL, prev_hash TEXT NOT NULL, event_hash TEXT NOT NULL,
            FOREIGN KEY(case_id) REFERENCES cases(case_id))""")


def _event(conn: sqlite3.Connection, case_id: str, kind: str, detail: dict) -> None:
    row = conn.execute("SELECT event_hash FROM case_events WHERE case_id = ? "
                       "ORDER BY event_id DESC LIMIT 1", (case_id,)).fetchone()
    previous = row[0] if row else "0" * 64
    event = {"kind": kind, "at": datetime.now(UTC).isoformat(), "detail": detail}
    payload = _canonical(event)
    digest = sha256((previous + payload).encode()).hexdigest()
    conn.execute("INSERT INTO case_events(case_id,event_json,prev_hash,event_hash) "
                 "VALUES(?,?,?,?)", (case_id, payload, previous, digest))


def ingest(db: Path, recipe: dict, alert: dict, scope: dict) -> dict:
    recipe = validate_recipe(recipe)
    scope = validate_scope(scope)
    normalized = parse_alert(alert, scope, recipe)
    initialize(db)
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT case_id,scope_json FROM cases WHERE workcell_id=? AND alert_id=?",
                           (recipe["workcell_id"], normalized["alert_id"])).fetchone()
        if row:
            if json.loads(row[1]) != scope:
                raise NetworkError("duplicate alert ID has a different scope")
            case_id = row[0]
        else:
            case_id = "case-" + uuid4().hex[:20]
            conn.execute("INSERT INTO cases(case_id,workcell_id,version,alert_id,scope_json,state,created_at) "
                         "VALUES(?,?,?,?,?,'received',?)",
                         (case_id, recipe["workcell_id"], recipe["version"],
                          normalized["alert_id"], _canonical(scope), datetime.now(UTC).isoformat()))
            _event(conn, case_id, "alert_ingested", normalized)
    return get_case(db, case_id)


def _case_row(conn: sqlite3.Connection, case_id: str) -> sqlite3.Row:
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM cases WHERE case_id=?", (case_id,)).fetchone()
    if row is None:
        raise NetworkError("case not found")
    return row


def get_case(db: Path, case_id: str) -> dict:
    if not db.exists():
        raise NetworkError("case not found")
    with closing(sqlite3.connect(db)) as conn:
        row = _case_row(conn, case_id)
        data = dict(row)
        for key in ("scope", "snapshot", "plan", "attempt", "outcome", "after"):
            raw = data.pop(key + "_json")
            data[key] = json.loads(raw) if raw else None
        events = conn.execute("SELECT event_json,prev_hash,event_hash FROM case_events "
                              "WHERE case_id=? ORDER BY event_id", (case_id,)).fetchall()
        data["events"] = [{**json.loads(item[0]), "prev_hash": item[1], "event_hash": item[2]}
                          for item in events]
    return data


def diagnose(db: Path, case_id: str, snapshot: dict) -> dict:
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        row = _case_row(conn, case_id)
        if snapshot.get("scope") != json.loads(row["scope_json"]):
            raise NetworkError("snapshot scope mismatch")
        if row["state"] != "received":
            if row["snapshot_json"] == _canonical(snapshot):
                return get_case(db, case_id)
            raise NetworkError("case is already diagnosed")
        plan = propose(snapshot)
        state = "diagnosed" if plan["action"] == "backup_now_candidate" else "needs_review"
        conn.execute("UPDATE cases SET state=?,snapshot_json=?,plan_json=? WHERE case_id=?",
                     (state, _canonical(snapshot), _canonical(plan), case_id))
        _event(conn, case_id, "diagnosed", {"action": plan["action"],
                                             "snapshot_sha256": plan["snapshot_sha256"]})
    return get_case(db, case_id)


def record_attempt(db: Path, case_id: str, attempt: dict) -> dict:
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        row = _case_row(conn, case_id)
        if row["state"] != "diagnosed":
            if row["attempt_json"] == _canonical(attempt):
                return get_case(db, case_id)
            raise NetworkError("case is not awaiting a reviewed retry")
        if attempt.get("scope") != json.loads(row["scope_json"]):
            raise NetworkError("attempt scope mismatch")
        plan = json.loads(row["plan_json"])
        if (attempt.get("based_on_failed_job_id") != plan["failed_job_id"]
                or attempt.get("plan_snapshot_sha256") != plan["snapshot_sha256"]):
            raise NetworkError("attempt is not bound to this diagnosis")
        if attempt.get("action") != "backup-now" or attempt.get("mutation_attempted") is not True:
            raise NetworkError("attempt is not a backup-now invocation")
        approvals = attempt.get("declared_approvals")
        if (attempt.get("cause_reviewed_declared") is not True
                or not isinstance(approvals, list)
                or not all(isinstance(item, str) for item in approvals)
                or not {"workload-owner", "backup-operator"}.issubset(set(approvals))):
            raise NetworkError("cause review and both declared approvals are required")
        state = "awaiting_result" if (attempt.get("request_status") == "accepted"
                                      and attempt.get("request_job_id")) else "needs_review"
        conn.execute("UPDATE cases SET state=?,attempt_json=? WHERE case_id=?",
                     (state, _canonical(attempt), case_id))
        _event(conn, case_id, "retry_recorded", {"request_status": attempt.get("request_status"),
                                                  "request_job_id": attempt.get("request_job_id")})
    return get_case(db, case_id)


def record_verification(db: Path, case_id: str, after: dict) -> dict:
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        row = _case_row(conn, case_id)
        if row["state"] not in ("awaiting_result", "verified"):
            raise NetworkError("case is not awaiting verification")
        if after.get("scope") != json.loads(row["scope_json"]):
            raise NetworkError("after snapshot scope mismatch")
        if after.get("account_verified") is not True:
            raise NetworkError("after snapshot Azure account scope is not verified")
        if row["after_json"] == _canonical(after):
            return get_case(db, case_id)
        if row["state"] == "verified":
            raise NetworkError("verified case is closed")
        outcome = verify(json.loads(row["snapshot_json"]), after,
                         json.loads(row["attempt_json"]))
        state = "verified" if outcome["status"] == "verified_backup" else "awaiting_result"
        conn.execute("UPDATE cases SET state=?,outcome_json=?,after_json=? WHERE case_id=?",
                     (state, _canonical(outcome), _canonical(after), case_id))
        _event(conn, case_id, "verification_recorded", {"status": outcome["status"]})
    return get_case(db, case_id)


def verify_chain(db: Path, case_id: str) -> bool:
    case = get_case(db, case_id)
    previous = "0" * 64
    for event in case["events"]:
        if not {"kind", "at", "detail"}.issubset(event):
            return False
        payload = {"kind": event["kind"], "at": event["at"], "detail": event["detail"]}
        if (event["prev_hash"] != previous or
                event["event_hash"] != sha256((previous + _canonical(payload)).encode()).hexdigest()):
            return False
        previous = event["event_hash"]
    return True
