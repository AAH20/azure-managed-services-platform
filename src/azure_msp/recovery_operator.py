"""Application-level recovery drill evaluation and local durable results."""

from __future__ import annotations

import ipaddress
import json
import math
import sqlite3
import time
from contextlib import closing
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .backup_resolution import validate_scope
from .service_delivery import RESOURCE_KEYS, _pilot
from .service_delivery import initialize as initialize_delivery


class DrillError(ValueError):
    """Invalid recovery contract, evidence, or probe configuration."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def _when(value: object, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        parsed = None
    if parsed is None or parsed.tzinfo is None:
        raise DrillError(f"{label} must be an ISO-8601 timestamp with timezone")
    return parsed.astimezone(UTC)


def validate_contract(value: dict) -> dict:
    if not isinstance(value, dict):
        raise DrillError("contract must be an object")
    scope = validate_scope(value.get("scope"))
    for key in ("workload_id", "environment_id"):
        if not isinstance(value.get(key), str) or not value[key].strip() or len(value[key]) > 100:
            raise DrillError(f"{key} is required")
    for key in ("rto_minutes", "rpo_minutes"):
        number = value.get(key)
        if not isinstance(number, int) or isinstance(number, bool) or not 1 <= number <= 10080:
            raise DrillError(f"{key} must be an integer from 1 to 10080")
    probe = value.get("probe")
    if not isinstance(probe, dict):
        raise DrillError("probe must be an object")
    url = probe.get("url")
    if not isinstance(url, str):
        raise DrillError("probe URL is required")
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        raise DrillError("probe URL has an invalid port") from None
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.fragment or parsed.query or port not in (None, 443)
            or parsed.hostname == "localhost" or parsed.hostname.endswith(".localhost")):
        raise DrillError("probe URL must be HTTPS without credentials, query or fragment")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise DrillError("probe URL cannot use a private or local IP literal")
    path = probe.get("json_path")
    if (not isinstance(path, str) or not path or len(path) > 120
            or any(not part.isidentifier() for part in path.split("."))):
        raise DrillError("probe json_path must be a dotted identifier path")
    expected = probe.get("equals")
    if not isinstance(expected, (str, int, float, bool)) or isinstance(expected, float):
        raise DrillError("probe equals must be a string, integer, or boolean")
    return {"schema_version": "1.0", "scope": scope, "workload_id": value["workload_id"].strip(),
            "environment_id": value["environment_id"].strip(), "rto_minutes": value["rto_minutes"],
            "rpo_minutes": value["rpo_minutes"],
            "probe": {"url": url, "json_path": path, "equals": expected}}


def _lookup(payload: dict, path: str) -> object:
    current: object = payload
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def probe_application(contract: dict, *, timeout_seconds: int = 10) -> dict:
    """Issue one unauthenticated read-only GET; never include response data in the result."""
    contract = validate_contract(contract)
    if not isinstance(timeout_seconds, int) or not 1 <= timeout_seconds <= 30:
        raise DrillError("timeout_seconds must be 1..30")
    request = Request(contract["probe"]["url"], headers={"Accept": "application/json",
                                                        "User-Agent": "azure-msp-recovery-operator/1"},
                      method="GET")
    started = time.monotonic()
    try:
        with build_opener(_NoRedirect).open(request, timeout=timeout_seconds) as response:
            status = response.status
            raw = response.read(65537)
        if len(raw) > 65536:
            raise DrillError("probe response exceeds 64 KiB")
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise DrillError("probe response must be a JSON object")
        actual = _lookup(payload, contract["probe"]["json_path"])
        expected = contract["probe"]["equals"]
        passed = status == 200 and type(actual) is type(expected) and actual == expected
        reason = "assertion_passed" if passed else "assertion_failed"
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        passed, reason = False, type(exc).__name__
        raw = b""
        status = exc.code if isinstance(exc, HTTPError) else None
    return {"checked_at": datetime.now(UTC).isoformat(), "passed": passed,
            "reason": reason, "http_status": status,
            "latency_ms": round((time.monotonic() - started) * 1000, 2),
            "response_sha256": sha256(raw).hexdigest() if raw else None,
            "probe_url": contract["probe"]["url"]}


def evaluate(contract: dict, evidence: dict, probe: dict) -> dict:
    contract = validate_contract(contract)
    if not isinstance(evidence, dict) or evidence.get("scope") != contract["scope"]:
        raise DrillError("restore evidence scope mismatch")
    if evidence.get("environment_id") != contract["environment_id"]:
        raise DrillError("restore environment mismatch")
    if evidence.get("isolated") is not True:
        raise DrillError("isolated restore confirmation is required")
    if not isinstance(evidence.get("restore_job_id"), str) or not evidence["restore_job_id"].strip():
        raise DrillError("restore_job_id is required")
    started = _when(evidence.get("started_at"), "started_at")
    restored = _when(evidence.get("restored_at"), "restored_at")
    point = _when(evidence.get("recovery_point_at"), "recovery_point_at")
    checked = _when(probe.get("checked_at") if isinstance(probe, dict) else None, "checked_at")
    cleaned = _when(evidence.get("cleanup_at"), "cleanup_at")
    if not point <= started <= restored <= checked <= cleaned:
        raise DrillError("recovery timestamps must be ordered")
    if probe.get("probe_url") != contract["probe"]["url"] or type(probe.get("passed")) is not bool:
        raise DrillError("probe result does not match contract")
    if evidence.get("cleanup_confirmed") is not True:
        raise DrillError("cleanup confirmation is required")
    rto_seconds = math.ceil((checked - started).total_seconds())
    rpo_seconds = math.ceil((started - point).total_seconds())
    checks = {"transaction": probe["passed"],
              "rto": probe["passed"] and rto_seconds <= contract["rto_minutes"] * 60,
              "rpo": rpo_seconds <= contract["rpo_minutes"] * 60,
              "isolated_cleanup": True}
    outcome = "passed" if all(checks.values()) else "failed"
    source = {"contract": contract, "evidence": evidence, "probe": probe}
    return {"schema_version": "1.0", "workload_id": contract["workload_id"],
            "scope": contract["scope"], "environment_id": contract["environment_id"],
            "restore_job_id": evidence["restore_job_id"], "outcome": outcome,
            "checks": checks, "elapsed_to_probe_seconds": rto_seconds,
            "actual_rto_seconds": rto_seconds if probe["passed"] else None,
            "actual_rpo_seconds": rpo_seconds,
            "probe_checked_at": checked.isoformat(), "source_sha256": sha256(
                json.dumps(source, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
            "boundary": "Operator-supplied restore and cleanup records are assertions; this result does not independently prove Azure restore or data consistency."}


def record_result(db: Path, pilot_id: str, report: dict) -> dict:
    pilot = _pilot(db, pilot_id)
    if pilot["state"] != "active":
        raise DrillError("pilot must be active")
    if not isinstance(report, dict) or report.get("outcome") not in ("passed", "failed"):
        raise DrillError("invalid drill report")
    scope = report.get("scope")
    if not isinstance(scope, dict) or any(scope.get(key) != pilot["scope"][key] for key in RESOURCE_KEYS):
        raise DrillError("drill is outside pilot scope")
    initialize_delivery(db)
    checked = _when(report.get("probe_checked_at"), "probe_checked_at")
    active = _when(pilot["active_at"], "active_at")
    if checked < active:
        raise DrillError("drill predates pilot activation")
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("""INSERT OR IGNORE INTO recovery_drills VALUES (?,?,?,?,?,?,?,?)""",
                     (report["source_sha256"], pilot_id, report["workload_id"], report["outcome"],
                      report["elapsed_to_probe_seconds"], report["actual_rto_seconds"],
                      report["actual_rpo_seconds"],
                      report["probe_checked_at"]))
        if report["outcome"] == "failed":
            conn.execute("""INSERT OR IGNORE INTO recovery_followups VALUES (?,?, 'open', ?)""",
                         (pilot_id, report["source_sha256"], datetime.now(UTC).isoformat()))
    return {"pilot_id": pilot_id, "workload_id": report["workload_id"],
            "outcome": report["outcome"], "source_sha256": report["source_sha256"]}


def list_followups(db: Path, pilot_id: str) -> list[dict]:
    """Operator-only local queue; never expose directly to an unauthenticated portal."""
    _pilot(db, pilot_id)
    initialize_delivery(db)
    with closing(sqlite3.connect(db)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""SELECT source_sha256,state,created_at
            FROM recovery_followups WHERE pilot_id=? ORDER BY created_at""", (pilot_id,)).fetchall()
    return [dict(row) for row in rows]
