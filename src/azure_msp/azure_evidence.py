from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Protocol

ADAPTER_VERSION = "0.2.0"


@dataclass(frozen=True)
class EvidenceReceipt:
    source: str
    tenant_id: str
    subscription_id: str
    collected_at: str
    adapter_version: str
    query_id: str
    raw_sha256: str
    status: str
    records: int
    error_class: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class Observation:
    source: str
    status: str
    data: object
    receipt: EvidenceReceipt

    def to_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "status": self.status,
            "data": self.data,
            "receipt": self.receipt.to_dict(),
        }


class CommandRunner(Protocol):
    def run(self, arguments: list[str]) -> subprocess.CompletedProcess[str]: ...


class AzureCliRunner:
    """Runs explicit Azure CLI argument arrays without invoking a shell."""

    def run(self, arguments: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["az", *arguments],
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )


def _receipt(
    *,
    source: str,
    tenant_id: str,
    subscription_id: str,
    query_id: str,
    raw: str,
    status: str,
    records: int,
    error_class: str | None = None,
) -> EvidenceReceipt:
    return EvidenceReceipt(
        source=source,
        tenant_id=tenant_id,
        subscription_id=subscription_id,
        collected_at=datetime.now(UTC).isoformat(),
        adapter_version=ADAPTER_VERSION,
        query_id=query_id,
        raw_sha256=sha256(raw.encode()).hexdigest(),
        status=status,
        records=records,
        error_class=error_class,
    )


def collect(
    runner: CommandRunner,
    *,
    source: str,
    tenant_id: str,
    subscription_id: str,
    query_id: str,
    arguments: list[str],
) -> Observation:
    result = runner.run([*arguments, "--subscription", subscription_id, "--output", "json"])
    raw = result.stdout if result.returncode == 0 else result.stderr
    if result.returncode != 0:
        return Observation(
            source=source,
            status="unknown",
            data=[],
            receipt=_receipt(
                source=source,
                tenant_id=tenant_id,
                subscription_id=subscription_id,
                query_id=query_id,
                raw=raw,
                status="unknown",
                records=0,
                error_class="azure_cli_error",
            ),
        )
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return Observation(
            source=source,
            status="unknown",
            data=[],
            receipt=_receipt(
                source=source,
                tenant_id=tenant_id,
                subscription_id=subscription_id,
                query_id=query_id,
                raw=raw,
                status="unknown",
                records=0,
                error_class="invalid_json",
            ),
        )
    records = len(data) if isinstance(data, list) else 1
    return Observation(
        source=source,
        status="observed",
        data=data,
        receipt=_receipt(
            source=source,
            tenant_id=tenant_id,
            subscription_id=subscription_id,
            query_id=query_id,
            raw=raw,
            status="observed",
            records=records,
        ),
    )


def collect_baseline(
    tenant_id: str, subscription_id: str, runner: CommandRunner | None = None
) -> list[Observation]:
    executor = runner or AzureCliRunner()
    specifications = [
        (
            "resource-graph",
            "resources-v1",
            ["graph", "query", "--query", "Resources | project id,name,type,location,tags,properties"],
        ),
        ("policy-insights", "policy-state-v1", ["policy", "state", "list"]),
        ("advisor", "advisor-recommendations-v1", ["advisor", "recommendation", "list"]),
        (
            "resource-health",
            "availability-statuses-2025-05-01",
            [
                "rest",
                "--method",
                "get",
                "--url",
                (
                    "https://management.azure.com/subscriptions/"
                    f"{subscription_id}/providers/Microsoft.ResourceHealth/"
                    "availabilityStatuses?api-version=2025-05-01"
                ),
            ],
        ),
        ("role-assignments", "rbac-v1", ["role", "assignment", "list", "--all"]),
    ]
    return [
        collect(
            executor,
            source=source,
            tenant_id=tenant_id,
            subscription_id=subscription_id,
            query_id=query_id,
            arguments=arguments,
        )
        for source, query_id, arguments in specifications
    ]
