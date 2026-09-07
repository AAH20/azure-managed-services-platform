from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256


class MigrationContractError(ValueError):
    """Raised when migration evidence or authority is incomplete."""


ALLOWED_STRATEGIES = {"retain", "retire", "rehost", "relocate", "replatform", "refactor"}


@dataclass(frozen=True)
class TargetCandidate:
    strategy: str
    target: str
    ready: bool
    annual_target_cost_usd: float
    implementation_cost_usd: float
    annual_operational_saving_usd: float
    annual_revenue_enablement_usd: float
    expected_downtime_loss_usd: float
    risk_reserve_usd: float

    @classmethod
    def from_dict(cls, value: dict) -> TargetCandidate:
        candidate = cls(
            strategy=value["strategy"],
            target=value["target"],
            ready=bool(value["ready"]),
            annual_target_cost_usd=float(value["annual_target_cost_usd"]),
            implementation_cost_usd=float(value["implementation_cost_usd"]),
            annual_operational_saving_usd=float(value["annual_operational_saving_usd"]),
            annual_revenue_enablement_usd=float(value["annual_revenue_enablement_usd"]),
            expected_downtime_loss_usd=float(value["expected_downtime_loss_usd"]),
            risk_reserve_usd=float(value["risk_reserve_usd"]),
        )
        if candidate.strategy not in ALLOWED_STRATEGIES:
            raise MigrationContractError(f"unsupported migration strategy: {candidate.strategy}")
        return candidate

    def first_year_net_value(self, annual_source_cost_usd: float) -> float:
        return round(
            annual_source_cost_usd
            + self.annual_operational_saving_usd
            + self.annual_revenue_enablement_usd
            - self.annual_target_cost_usd
            - self.implementation_cost_usd
            - self.expected_downtime_loss_usd
            - self.risk_reserve_usd,
            2,
        )


@dataclass(frozen=True)
class MigrationAsset:
    asset_id: str
    name: str
    source_platform: str
    annual_source_cost_usd: float
    dependencies: tuple[str, ...]
    candidates: tuple[TargetCandidate, ...]

    @classmethod
    def from_dict(cls, value: dict) -> MigrationAsset:
        return cls(
            asset_id=value["asset_id"],
            name=value["name"],
            source_platform=value["source_platform"],
            annual_source_cost_usd=float(value["annual_source_cost_usd"]),
            dependencies=tuple(value.get("dependencies", [])),
            candidates=tuple(TargetCandidate.from_dict(item) for item in value["candidates"]),
        )


@dataclass(frozen=True)
class CutoverEvidence:
    asset_id: str
    test_migration_passed: bool
    network_verified: bool
    identity_verified: bool
    dns_verified: bool
    backup_verified: bool
    rollback_verified: bool
    transaction_verified: bool
    observed_downtime_minutes: int
    observed_rpo_minutes: int

    @classmethod
    def from_dict(cls, value: dict) -> CutoverEvidence:
        return cls(
            asset_id=value["asset_id"],
            test_migration_passed=bool(value["test_migration_passed"]),
            network_verified=bool(value["network_verified"]),
            identity_verified=bool(value["identity_verified"]),
            dns_verified=bool(value["dns_verified"]),
            backup_verified=bool(value["backup_verified"]),
            rollback_verified=bool(value["rollback_verified"]),
            transaction_verified=bool(value["transaction_verified"]),
            observed_downtime_minutes=int(value["observed_downtime_minutes"]),
            observed_rpo_minutes=int(value["observed_rpo_minutes"]),
        )


def dependency_order(assets: list[MigrationAsset]) -> list[str]:
    by_id = {asset.asset_id: asset for asset in assets}
    if len(by_id) != len(assets):
        raise MigrationContractError("asset ids must be unique")
    for asset in assets:
        missing = set(asset.dependencies) - set(by_id)
        if missing:
            raise MigrationContractError(
                f"asset {asset.asset_id} has unknown dependencies: {sorted(missing)}"
            )
    visiting: set[str] = set()
    visited: set[str] = set()
    ordered: list[str] = []

    def visit(asset_id: str) -> None:
        if asset_id in visiting:
            raise MigrationContractError("dependency graph contains a cycle")
        if asset_id in visited:
            return
        visiting.add(asset_id)
        for dependency in by_id[asset_id].dependencies:
            visit(dependency)
        visiting.remove(asset_id)
        visited.add(asset_id)
        ordered.append(asset_id)

    for asset_id in sorted(by_id):
        visit(asset_id)
    return ordered


def _assess_asset(asset: MigrationAsset) -> dict[str, object]:
    candidates = []
    for candidate in asset.candidates:
        candidates.append(
            {
                **asdict(candidate),
                "first_year_net_value_usd": candidate.first_year_net_value(
                    asset.annual_source_cost_usd
                ),
            }
        )
    ready = [item for item in candidates if item["ready"]]
    selected = max(ready, key=lambda item: item["first_year_net_value_usd"]) if ready else None
    return {
        "asset_id": asset.asset_id,
        "source_platform": asset.source_platform,
        "annual_source_cost_usd": asset.annual_source_cost_usd,
        "candidates": candidates,
        "recommended_candidate": selected,
        "recommendation_status": "decision-support-only" if selected else "blocked-no-ready-target",
    }


def _cutover_decision(
    evidence: CutoverEvidence | None,
    *,
    maximum_downtime_minutes: int,
    maximum_rpo_minutes: int,
    missing_approvals: set[str],
) -> dict[str, object]:
    if evidence is None:
        return {"decision": "blocked", "reasons": ["cutover evidence is missing"]}
    checks = {
        "test_migration": evidence.test_migration_passed,
        "network": evidence.network_verified,
        "identity": evidence.identity_verified,
        "dns": evidence.dns_verified,
        "backup": evidence.backup_verified,
        "rollback": evidence.rollback_verified,
        "business_transaction": evidence.transaction_verified,
    }
    reasons = [f"{name} verification failed" for name, passed in checks.items() if not passed]
    if evidence.observed_downtime_minutes > maximum_downtime_minutes:
        reasons.append("observed downtime exceeds the contracted maximum")
    if evidence.observed_rpo_minutes > maximum_rpo_minutes:
        reasons.append("observed RPO exceeds the contracted maximum")
    if missing_approvals:
        reasons.append(f"missing required approvals: {sorted(missing_approvals)}")
    return {
        "decision": "eligible-for-controlled-cutover" if not reasons else "blocked",
        "checks": checks,
        "observed_downtime_minutes": evidence.observed_downtime_minutes,
        "observed_rpo_minutes": evidence.observed_rpo_minutes,
        "reasons": reasons,
    }


def evaluate_migration(value: dict, *, approvals: set[str]) -> dict[str, object]:
    assets = [MigrationAsset.from_dict(item) for item in value["assets"]]
    if not assets:
        raise MigrationContractError("at least one migration asset is required")
    evidence = {
        item.asset_id: item
        for item in (CutoverEvidence.from_dict(raw) for raw in value["cutover_evidence"])
    }
    order = dependency_order(assets)
    contract = value["cutover_contract"]
    required = set(contract["required_approvers"])
    missing_approvals = required - approvals
    assessments = [_assess_asset(asset) for asset in assets]
    decisions = {
        asset_id: _cutover_decision(
            evidence.get(asset_id),
            maximum_downtime_minutes=int(contract["maximum_downtime_minutes"]),
            maximum_rpo_minutes=int(contract["maximum_rpo_minutes"]),
            missing_approvals=missing_approvals,
        )
        for asset_id in order
    }
    wave_ready = all(item["decision"] == "eligible-for-controlled-cutover" for item in decisions.values())
    source_decommission = {
        "decision": "separate-approval-required",
        "approved": False,
        "source_mutations_executed": 0,
    }
    evidence_seed = f"{assets}:{order}:{decisions}:{assessments}"
    return {
        "schema_version": "1.0",
        "migration_id": value["migration_id"],
        "customer_id": value["customer_id"],
        "dependency_order": order,
        "assessments": assessments,
        "cutover_decisions": decisions,
        "wave_status": "eligible-for-controlled-cutover" if wave_ready else "blocked",
        "source_decommission": source_decommission,
        "evidence_sha256": sha256(evidence_seed.encode()).hexdigest(),
        "cloud_mutations_executed": 0,
        "limitations": [
            "The included estate and economics are synthetic, not a live Azure Migrate assessment.",
            "Recommendations are decision support and do not authorize migration execution.",
            "Source decommissioning requires separate post-cutover approval and validation.",
        ],
    }
