from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256


class NetworkAssuranceError(ValueError):
    """Raised when connectivity intent or path evidence is invalid."""


@dataclass(frozen=True)
class ConnectivityIntent:
    intent_id: str
    business_service: str
    source: str
    destination: str
    protocol: str
    port: int
    must_be_reachable: bool
    required_transit: tuple[str, ...]
    prohibited_transit: tuple[str, ...]
    expected_dns_scope: str
    maximum_latency_ms: float
    contribution_margin_per_hour_usd: float

    @classmethod
    def from_dict(cls, value: dict) -> ConnectivityIntent:
        scope = value["expected_dns_scope"]
        if scope not in {"private", "public", "not-applicable"}:
            raise NetworkAssuranceError(f"unsupported DNS scope: {scope}")
        return cls(
            intent_id=value["intent_id"], business_service=value["business_service"],
            source=value["source"], destination=value["destination"],
            protocol=value["protocol"].lower(), port=int(value["port"]),
            must_be_reachable=bool(value["must_be_reachable"]),
            required_transit=tuple(value.get("required_transit", [])),
            prohibited_transit=tuple(value.get("prohibited_transit", [])),
            expected_dns_scope=scope, maximum_latency_ms=float(value["maximum_latency_ms"]),
            contribution_margin_per_hour_usd=float(value["contribution_margin_per_hour_usd"]),
        )


@dataclass(frozen=True)
class PathObservation:
    snapshot: str
    intent_id: str
    reachable: bool
    return_reachable: bool
    path: tuple[str, ...]
    resolved_address: str | None
    dns_scope: str
    latency_ms: float | None
    blocking_control: str | None

    @classmethod
    def from_dict(cls, value: dict) -> PathObservation:
        if value["snapshot"] not in {"current", "proposed"}:
            raise NetworkAssuranceError("snapshot must be current or proposed")
        latency = value.get("latency_ms")
        return cls(
            snapshot=value["snapshot"], intent_id=value["intent_id"],
            reachable=bool(value["reachable"]), return_reachable=bool(value["return_reachable"]),
            path=tuple(value.get("path", [])), resolved_address=value.get("resolved_address"),
            dns_scope=value.get("dns_scope", "not-applicable"),
            latency_ms=float(latency) if latency is not None else None,
            blocking_control=value.get("blocking_control"),
        )


def evaluate_path(intent: ConnectivityIntent, observation: PathObservation) -> dict[str, object]:
    failures = []
    if observation.reachable != intent.must_be_reachable:
        expected = "reachable" if intent.must_be_reachable else "unreachable"
        failures.append(f"flow must remain {expected}")
    if intent.must_be_reachable and observation.reachable and not observation.return_reachable:
        failures.append("return path is not reachable")
    if observation.reachable:
        missing = set(intent.required_transit) - set(observation.path)
        prohibited = set(intent.prohibited_transit) & set(observation.path)
        if missing:
            failures.append(f"required transit is missing: {sorted(missing)}")
        if prohibited:
            failures.append(f"prohibited transit is present: {sorted(prohibited)}")
    if intent.expected_dns_scope != "not-applicable" and observation.dns_scope != intent.expected_dns_scope:
        failures.append(
            f"DNS scope {observation.dns_scope!r} differs from {intent.expected_dns_scope!r}"
        )
    if observation.latency_ms is not None and observation.latency_ms > intent.maximum_latency_ms:
        failures.append("latency exceeds the contracted maximum")
    return {
        "snapshot": observation.snapshot, "status": "pass" if not failures else "fail",
        "failures": failures, "path": list(observation.path),
        "resolved_address": observation.resolved_address,
        "blocking_control": observation.blocking_control,
    }


def evaluate_network_change(value: dict, *, approvals: set[str]) -> dict[str, object]:
    intents = [ConnectivityIntent.from_dict(item) for item in value["connectivity_intents"]]
    if not intents:
        raise NetworkAssuranceError("at least one connectivity intent is required")
    by_id = {item.intent_id: item for item in intents}
    if len(by_id) != len(intents):
        raise NetworkAssuranceError("connectivity intent ids must be unique")
    indexed = {}
    observations = [PathObservation.from_dict(item) for item in value["path_observations"]]
    for observation in observations:
        if observation.intent_id not in by_id:
            raise NetworkAssuranceError(f"observation has unknown intent: {observation.intent_id}")
        key = (observation.intent_id, observation.snapshot)
        if key in indexed:
            raise NetworkAssuranceError(f"duplicate path observation: {key}")
        indexed[key] = observation

    comparisons, regressions = [], []
    margin_at_risk = 0.0
    for intent in intents:
        missing = [s for s in ("current", "proposed") if (intent.intent_id, s) not in indexed]
        if missing:
            raise NetworkAssuranceError(f"intent {intent.intent_id} is missing snapshots: {missing}")
        current = evaluate_path(intent, indexed[(intent.intent_id, "current")])
        proposed = evaluate_path(intent, indexed[(intent.intent_id, "proposed")])
        regression = current["status"] == "pass" and proposed["status"] == "fail"
        if regression:
            regressions.append(intent.intent_id)
            margin_at_risk += intent.contribution_margin_per_hour_usd
        comparisons.append({
            "intent": asdict(intent), "current": current, "proposed": proposed,
            "behavior_regression": regression,
        })

    required = set(value["change_contract"]["required_approvers"])
    missing_approvals = required - approvals
    blockers = []
    if regressions:
        blockers.append(f"connectivity regressions: {sorted(regressions)}")
    if missing_approvals:
        blockers.append(f"missing required approvals: {sorted(missing_approvals)}")
    decision = "blocked" if blockers else "eligible-for-change-assurance"
    seed = f"{intents}:{observations}:{decision}:{blockers}"
    return {
        "schema_version": "1.0", "change_id": value["change_id"],
        "customer_id": value["customer_id"], "comparisons": comparisons,
        "decision": decision, "blockers": blockers,
        "economics": {"contribution_margin_at_risk_per_hour_usd": round(margin_at_risk, 2),
                      "verified_loss_avoided_usd": 0.0},
        "rollback_required": decision == "blocked", "production_deployment_authorized": False,
        "evidence_sha256": sha256(seed.encode()).hexdigest(), "cloud_mutations_executed": 0,
        "limitations": [
            "The included topology, paths and economics are synthetic.",
            "Path observations are adapter inputs, not live packet captures or Azure results.",
            "An eligible decision still requires Change Assurance and active canary probes.",
        ],
    }
