"""Paired AI endpoint benchmark with accepted-work unit economics."""

from __future__ import annotations

import json
import math
import re
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path

from .inference_fleet import deployment_plan
from .partner_cloud import _id, authenticate, get_order

SHA256 = re.compile(r"^[0-9a-f]{64}$")
EVENT_FIELDS = {"scenario_id", "ok", "accepted", "acceptance_ref", "latency_ms",
                "ttft_ms", "output_tokens", "request_cost_usd"}


class AIServiceError(ValueError):
    """Invalid workload contract, benchmark evidence, or partner scope."""


def plan_service(db: Path, partner_id: str, order_id: str, fleet_spec: dict) -> dict:
    """Reuse the pinned single-GPU planner for an existing partner order."""
    order = get_order(db, partner_id, order_id)
    if order is None:
        raise AIServiceError("partner order not found")
    return deployment_plan(order, fleet_spec)


def _money(value: object, label: str) -> Decimal:
    try:
        amount = Decimal(str(value))
    except (TypeError, InvalidOperation):
        raise AIServiceError(f"{label} must be nonnegative USD") from None
    if not amount.is_finite() or amount < 0 or amount.as_tuple().exponent < -6:
        raise AIServiceError(f"{label} must be nonnegative USD with at most six decimals")
    return amount


def _number(value: object, label: str, *, maximum: float | None = None) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise AIServiceError(f"{label} must be a finite nonnegative number")
    if maximum is not None and value > maximum:
        raise AIServiceError(f"{label} exceeds {maximum}")
    return float(value)


def _digest(value: object, label: str) -> str:
    if not isinstance(value, str) or not SHA256.fullmatch(value):
        raise AIServiceError(f"{label} must be a SHA-256 hex digest")
    return value


def validate_contract(value: dict) -> dict:
    if not isinstance(value, dict) or value.get("schema_version") != "1.0":
        raise AIServiceError("contract schema_version must be 1.0")
    partner_id = _id(value.get("partner_id"), "partner_id")
    order_id = _id(value.get("order_id"), "order_id")
    workload_id = _id(value.get("workload_id"), "workload_id")
    workload_sha256 = _digest(value.get("workload_sha256"), "workload_sha256")
    targets = value.get("targets")
    if not isinstance(targets, dict):
        raise AIServiceError("targets must be an object")
    minimum = targets.get("minimum_requests")
    if type(minimum) is not int or minimum < 2:
        raise AIServiceError("minimum_requests must be an integer of at least 2")
    normalized = {"minimum_requests": minimum}
    for key in ("minimum_success_pct", "minimum_acceptance_pct"):
        normalized[key] = _number(targets.get(key), key, maximum=100)
    for key in ("maximum_p95_latency_ms", "maximum_p95_ttft_ms"):
        number = _number(targets.get(key), key)
        if number == 0:
            raise AIServiceError(f"{key} must be positive")
        normalized[key] = number
    cap = _money(targets.get("maximum_cost_per_accepted_usd"), "maximum_cost_per_accepted_usd")
    if cap == 0:
        raise AIServiceError("maximum_cost_per_accepted_usd must be positive")
    normalized["maximum_cost_per_accepted_usd"] = str(cap)
    return {"schema_version": "1.0", "partner_id": partner_id, "order_id": order_id,
            "workload_id": workload_id, "workload_sha256": workload_sha256,
            "targets": normalized}


def _events(value: object, label: str) -> list[dict]:
    if not isinstance(value, list) or not value:
        raise AIServiceError(f"{label}.events must be a nonempty list")
    results = []
    seen = set()
    for item in value:
        if not isinstance(item, dict) or set(item) != EVENT_FIELDS:
            raise AIServiceError(f"{label} events must contain exactly the allowed metric fields")
        scenario = _id(item["scenario_id"], "scenario_id")
        if scenario in seen:
            raise AIServiceError(f"duplicate scenario_id in {label}")
        seen.add(scenario)
        if type(item["ok"]) is not bool or type(item["accepted"]) is not bool:
            raise AIServiceError("ok and accepted must be booleans")
        if item["accepted"] and not item["ok"]:
            raise AIServiceError("a failed request cannot be accepted")
        ref = item["acceptance_ref"]
        if item["accepted"] and (not isinstance(ref, str) or not ref.strip() or len(ref) > 160):
            raise AIServiceError("accepted result requires an acceptance evidence reference")
        if ref is not None and not isinstance(ref, str):
            raise AIServiceError("acceptance_ref must be text or null")
        latency = _number(item["latency_ms"], "latency_ms")
        ttft = _number(item["ttft_ms"], "ttft_ms")
        if item["ok"] and (latency == 0 or ttft == 0 or ttft > latency):
            raise AIServiceError("successful request needs valid positive latency and TTFT")
        tokens = item["output_tokens"]
        if type(tokens) is not int or tokens < 0 or (item["ok"] and tokens == 0):
            raise AIServiceError("output_tokens must be nonnegative and positive on success")
        cost = _money(item["request_cost_usd"], "request_cost_usd")
        results.append({"scenario_id": scenario, "ok": item["ok"],
                        "accepted": item["accepted"], "acceptance_ref": ref,
                        "latency_ms": latency, "ttft_ms": ttft,
                        "output_tokens": tokens, "request_cost_usd": str(cost)})
    return results


def _run(value: object, label: str, workload_sha256: str) -> dict:
    if not isinstance(value, dict) or set(value) != {"provider", "model", "workload_sha256",
                                                "gpu_hours", "gpu_hour_usd", "other_fixed_usd",
                                                "events"}:
        raise AIServiceError(f"{label} must contain exact run fields")
    for key in ("provider", "model"):
        if not isinstance(value[key], str) or not value[key].strip() or len(value[key]) > 160:
            raise AIServiceError(f"{label}.{key} is required")
    if _digest(value["workload_sha256"], f"{label}.workload_sha256") != workload_sha256:
        raise AIServiceError("run workload digest does not match contract")
    return {"provider": value["provider"].strip(), "model": value["model"].strip(),
            "workload_sha256": workload_sha256,
            "gpu_hours": str(_money(value["gpu_hours"], "gpu_hours")),
            "gpu_hour_usd": str(_money(value["gpu_hour_usd"], "gpu_hour_usd")),
            "other_fixed_usd": str(_money(value["other_fixed_usd"], "other_fixed_usd")),
            "events": _events(value["events"], label)}


def _p95(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[math.ceil(0.95 * len(ordered)) - 1]


def _total_cost(run: dict) -> Decimal:
    return (Decimal(run["gpu_hours"]) * Decimal(run["gpu_hour_usd"])
            + Decimal(run["other_fixed_usd"])
            + sum((Decimal(event["request_cost_usd"]) for event in run["events"]), Decimal(0)))


def _summary(run: dict) -> dict:
    events = run["events"]
    successes = [event for event in events if event["ok"]]
    accepted = sum(event["accepted"] for event in events)
    total = _total_cost(run)
    cost_per = total / accepted if accepted else None
    return {"provider": run["provider"], "model": run["model"], "requests": len(events),
            "successful_requests": len(successes), "accepted_results": accepted,
            "success_pct": round(100 * len(successes) / len(events), 2),
            "acceptance_pct": round(100 * accepted / len(events), 2),
            "p95_latency_ms": _p95([event["latency_ms"] for event in successes]),
            "p95_ttft_ms": _p95([event["ttft_ms"] for event in successes]),
            "output_tokens": sum(event["output_tokens"] for event in events),
            "total_test_window_cost_usd": str(total.quantize(Decimal("0.0001"))),
            "cost_per_accepted_usd": str(cost_per.quantize(Decimal("0.0001"))) if cost_per is not None else None}


def evaluate(contract: dict, evidence: dict) -> dict:
    contract = validate_contract(contract)
    if not isinstance(evidence, dict) or set(evidence) != {"baseline", "candidate"}:
        raise AIServiceError("evidence must have baseline and candidate runs")
    baseline = _run(evidence["baseline"], "baseline", contract["workload_sha256"])
    candidate = _run(evidence["candidate"], "candidate", contract["workload_sha256"])
    left = {event["scenario_id"] for event in baseline["events"]}
    right = {event["scenario_id"] for event in candidate["events"]}
    if left != right:
        raise AIServiceError("baseline and candidate must use the same scenario IDs")
    base = _summary(baseline)
    newer = _summary(candidate)
    targets = contract["targets"]
    base_cost = _total_cost(baseline) / base["accepted_results"] if base["accepted_results"] else None
    new_cost = _total_cost(candidate) / newer["accepted_results"] if newer["accepted_results"] else None
    gates = {"sample_coverage": newer["requests"] >= targets["minimum_requests"],
             "success": Decimal(100 * newer["successful_requests"]) / newer["requests"] >= Decimal(str(targets["minimum_success_pct"])),
             "acceptance": Decimal(100 * newer["accepted_results"]) / newer["requests"] >= Decimal(str(targets["minimum_acceptance_pct"])),
             "latency": newer["p95_latency_ms"] is not None and newer["p95_latency_ms"] <= targets["maximum_p95_latency_ms"],
             "ttft": newer["p95_ttft_ms"] is not None and newer["p95_ttft_ms"] <= targets["maximum_p95_ttft_ms"],
             "cost": new_cost is not None and new_cost <= Decimal(targets["maximum_cost_per_accepted_usd"]),
             "no_acceptance_regression": newer["accepted_results"] >= base["accepted_results"],
             "no_cost_regression": new_cost is not None and base_cost is not None and new_cost <= base_cost}
    canonical = {"contract": contract, "baseline": baseline, "candidate": candidate}
    return {"schema_version": "1.0", "partner_id": contract["partner_id"],
            "order_id": contract["order_id"], "workload_id": contract["workload_id"],
            "workload_sha256": contract["workload_sha256"], "baseline": base,
            "candidate": newer, "gates": gates,
            "decision": "eligible_for_operator_review" if all(gates.values()) else "hold",
            "failed_gates": sorted(key for key, passed in gates.items() if not passed),
            "evidence_sha256": sha256(json.dumps(canonical, sort_keys=True,
                                                separators=(",", ":")).encode()).hexdigest(),
            "boundary": "Imported paired benchmark and acceptance references are operator assertions. No endpoint traffic, cluster change, billing event, or customer outcome was independently observed."}


def initialize(db: Path) -> None:
    db.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS ai_service_scores (
            evidence_sha256 TEXT PRIMARY KEY, partner_id TEXT NOT NULL, order_id TEXT NOT NULL,
            decision TEXT NOT NULL, report_json TEXT NOT NULL, recorded_at TEXT NOT NULL)""")


def record_score(db: Path, report: dict) -> dict:
    if not isinstance(report, dict) or report.get("decision") not in ("hold", "eligible_for_operator_review"):
        raise AIServiceError("invalid scorecard")
    order = get_order(db, report.get("partner_id"), report.get("order_id"))
    if order is None:
        raise AIServiceError("partner order not found")
    if report.get("candidate", {}).get("model") != order["model"]:
        raise AIServiceError("candidate model does not match partner order")
    initialize(db)
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("INSERT OR IGNORE INTO ai_service_scores VALUES (?,?,?,?,?,?)",
                     (report["evidence_sha256"], report["partner_id"], report["order_id"],
                      report["decision"], json.dumps(report, sort_keys=True),
                      datetime.now(UTC).isoformat()))
    return {"order_id": report["order_id"], "evidence_sha256": report["evidence_sha256"],
            "decision": report["decision"]}


def partner_scores(db: Path, partner_id: str, token: str, order_id: str) -> list[dict]:
    if not authenticate(db, partner_id, token):
        raise AIServiceError("partner authentication failed")
    if get_order(db, partner_id, order_id) is None:
        raise AIServiceError("partner order not found")
    initialize(db)
    with closing(sqlite3.connect(db)) as conn:
        rows = conn.execute("SELECT report_json FROM ai_service_scores WHERE partner_id=? AND order_id=? "
                            "ORDER BY recorded_at", (partner_id, order_id)).fetchall()
    return [json.loads(row[0]) for row in rows]
