from __future__ import annotations

import argparse
import json
from pathlib import Path

from .evaluator import evaluate, kpis, proposals
from .models import Customer, Resource
from .operations import build_work_queue, build_workloads
from .report import render_customer_report


def run(
    input_path: Path, output_path: Path, html_output: Path | None = None
) -> dict[str, object]:
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    customer_data = payload.get("customer") or {
        "customer_id": "unassigned",
        "name": "Unassigned customer",
        "tenant_id": "unknown",
    }
    customer = Customer(**customer_data)
    resources = [Resource.from_dict(item) for item in payload["resources"]]
    findings = evaluate(resources)
    change_proposals = proposals(findings)
    workloads = build_workloads(customer, resources, payload.get("workloads", []))
    work_queue = build_work_queue(customer, workloads, findings, change_proposals)
    metrics = kpis(resources, findings)
    report = {
        "schema_version": "1.0",
        "source": payload.get("source", "unknown"),
        "evidence_class": payload.get("evidence_class", "unverified"),
        "customer": customer_data,
        "kpis": metrics,
        "workloads": [
            {
                "workload_id": item.workload_id,
                "name": item.name,
                "criticality": item.criticality,
                "resource_ids": list(item.resource_ids),
            }
            for item in workloads
        ],
        "findings": [item.to_dict() for item in findings],
        "change_proposals": [item.to_dict() for item in change_proposals],
        "work_queue": [item.to_dict() for item in work_queue],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if html_output:
        render_customer_report(customer, workloads, work_queue, metrics, html_output)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate an Azure inventory snapshot")
    parser.add_argument("inventory", type=Path)
    parser.add_argument("--output", type=Path, default=Path("evidence/report.json"))
    parser.add_argument("--html", type=Path, help="Optional customer-facing HTML report")
    args = parser.parse_args()
    report = run(args.inventory, args.output, args.html)
    print(json.dumps(report["kpis"], indent=2))


if __name__ == "__main__":
    main()
