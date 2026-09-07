from __future__ import annotations

import argparse
import json
from pathlib import Path

from .evaluator import evaluate, kpis, proposals
from .models import Resource


def run(input_path: Path, output_path: Path) -> dict[str, object]:
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    resources = [Resource.from_dict(item) for item in payload["resources"]]
    findings = evaluate(resources)
    report = {
        "schema_version": "1.0",
        "source": payload.get("source", "unknown"),
        "evidence_class": payload.get("evidence_class", "unverified"),
        "kpis": kpis(resources, findings),
        "findings": [item.to_dict() for item in findings],
        "change_proposals": [item.to_dict() for item in proposals(findings)],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate an Azure inventory snapshot")
    parser.add_argument("inventory", type=Path)
    parser.add_argument("--output", type=Path, default=Path("evidence/report.json"))
    args = parser.parse_args()
    report = run(args.inventory, args.output)
    print(json.dumps(report["kpis"], indent=2))


if __name__ == "__main__":
    main()

