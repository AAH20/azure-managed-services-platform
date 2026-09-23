"""Operator workflow for one scoped Azure VM backup incident."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from .azure_evidence import AzureCliRunner, CommandRunner
from .backup_resolution import collect_snapshot, propose, retry_backup
from .recovery_operator import evaluate as evaluate_drill
from .recovery_operator import record_result as record_pilot_drill
from .resolution_network import (
    diagnose,
    get_case,
    ingest,
    record_attempt,
    record_verification,
    verify_chain,
)


class OperatorError(ValueError):
    """Unsafe or inconsistent operator transition."""


def initialize(db: Path) -> None:
    db.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS operator_writes (
            case_id TEXT PRIMARY KEY, state TEXT NOT NULL, started_at TEXT NOT NULL,
            attempt_json TEXT, error_class TEXT)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS operator_drills (
            case_id TEXT NOT NULL, source_sha256 TEXT NOT NULL, outcome TEXT NOT NULL,
            checked_at TEXT NOT NULL, attached_at TEXT NOT NULL,
            PRIMARY KEY(case_id, source_sha256))""")


def open_case(db: Path, recipe: dict, alert: dict, scope: dict) -> dict:
    return ingest(db, recipe, alert, scope)


def diagnose_case(db: Path, case_id: str, *, snapshot: dict | None = None,
                  runner: CommandRunner | None = None) -> dict:
    case = get_case(db, case_id)
    observed = snapshot if snapshot is not None else collect_snapshot(case["scope"], runner)
    return diagnose(db, case_id, observed)


def execute_retry(db: Path, case_id: str, *, approvals: set[str], cause_reviewed: bool,
                  acknowledge_cloud_write: bool, current: dict | None = None,
                  runner: CommandRunner | None = None) -> dict:
    if not cause_reviewed or not acknowledge_cloud_write:
        raise OperatorError("cause review and explicit cloud-write acknowledgement are required")
    if not {"workload-owner", "backup-operator"}.issubset(approvals):
        raise OperatorError("workload-owner and backup-operator approvals are required")
    case = get_case(db, case_id)
    if case["state"] != "diagnosed" or case["plan"]["action"] != "backup_now_candidate":
        raise OperatorError("case is not awaiting a reviewed backup retry")
    fresh = current if current is not None else collect_snapshot(case["scope"], runner)
    if fresh.get("scope") != case["scope"] or fresh.get("account_verified") is not True:
        raise OperatorError("fresh Azure account and scope must be verified")
    fresh_plan = propose(fresh)
    if (fresh_plan["action"] != "backup_now_candidate"
            or fresh_plan["failed_job_id"] != case["plan"]["failed_job_id"]):
        raise OperatorError("backup condition changed; collect and review again")
    initialize(db)
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute("SELECT 1 FROM operator_writes WHERE case_id=?", (case_id,)).fetchone():
            raise OperatorError("write intent already exists; reconcile it before any retry")
        state = conn.execute("SELECT state FROM cases WHERE case_id=?", (case_id,)).fetchone()
        if state is None or state[0] != "diagnosed":
            raise OperatorError("case state changed before write intent")
        conn.execute("INSERT INTO operator_writes(case_id,state,started_at) VALUES (?,'started',?)",
                     (case_id, datetime.now(UTC).isoformat()))
    try:
        attempt = retry_backup(case["scope"], case["plan"], fresh, approvals,
                               runner or AzureCliRunner())
        attempt["cause_reviewed_declared"] = True
        updated = record_attempt(db, case_id, attempt)
        with closing(sqlite3.connect(db)) as conn, conn:
            conn.execute("UPDATE operator_writes SET state=?,attempt_json=? WHERE case_id=?",
                         ("recorded" if (attempt["request_status"] == "accepted"
                                         and attempt.get("request_job_id")) else "uncertain",
                          json.dumps(attempt, sort_keys=True), case_id))
        return updated
    except Exception as exc:
        with closing(sqlite3.connect(db)) as conn, conn:
            conn.execute("UPDATE operator_writes SET state='uncertain',error_class=? WHERE case_id=?",
                         (type(exc).__name__, case_id))
        raise


def verify_case(db: Path, case_id: str, *, after: dict | None = None,
                runner: CommandRunner | None = None) -> dict:
    case = get_case(db, case_id)
    observed = after if after is not None else collect_snapshot(case["scope"], runner)
    return record_verification(db, case_id, observed)


def attach_drill(db: Path, case_id: str, contract: dict, restore: dict, probe: dict,
                 *, pilot_id: str | None = None) -> dict:
    case = get_case(db, case_id)
    if case["state"] != "verified" or case["outcome"]["status"] != "verified_backup":
        raise OperatorError("backup must be verified before a drill can be attached")
    report = evaluate_drill(contract, restore, probe)
    if report["scope"] != case["scope"]:
        raise OperatorError("drill scope does not match case")
    verification = next(event for event in reversed(case["events"])
                        if event["kind"] == "verification_recorded")
    checked = datetime.fromisoformat(report["probe_checked_at"])
    if checked < datetime.fromisoformat(verification["at"]):
        raise OperatorError("drill predates backup verification")
    new_ids = set(case["outcome"]["new_recovery_point_ids"])
    points = case["after"]["recovery_points"]
    if not any(point["point_id"] in new_ids
               and datetime.fromisoformat(point["created_at"]) == datetime.fromisoformat(
                   restore["recovery_point_at"]) for point in points):
        raise OperatorError("drill did not use a newly verified recovery point")
    initialize(db)
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("INSERT OR IGNORE INTO operator_drills VALUES (?,?,?,?,?)",
                     (case_id, report["source_sha256"], report["outcome"],
                      report["probe_checked_at"], datetime.now(UTC).isoformat()))
    if pilot_id is not None:
        report["service_delivery"] = record_pilot_drill(db, pilot_id, report)
    return report


def case_report(db: Path, case_id: str) -> dict:
    case = get_case(db, case_id)
    initialize(db)
    with closing(sqlite3.connect(db)) as conn:
        conn.row_factory = sqlite3.Row
        writes = conn.execute("SELECT state,started_at,error_class FROM operator_writes WHERE case_id=?",
                              (case_id,)).fetchone()
        drills = conn.execute("SELECT outcome,checked_at FROM operator_drills WHERE case_id=? "
                              "ORDER BY checked_at", (case_id,)).fetchall()
    return {"case_id": case_id, "workcell_id": case["workcell_id"],
            "state": case["state"], "backup_result": case["outcome"]["status"] if case["outcome"] else None,
            "write_intent": dict(writes) if writes else None,
            "drills": [dict(row) for row in drills], "event_chain_valid": verify_chain(db, case_id),
            "boundary": "A completed backup job and new recovery point do not prove restore. Drill results rely on operator-supplied restore and cleanup assertions."}
