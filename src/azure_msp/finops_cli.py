from __future__ import annotations

import argparse
import json
from pathlib import Path

from .finops import evaluate_value_realization


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate Azure workload unit economics")
    parser.add_argument("evidence", type=Path)
    parser.add_argument("--approval", action="append", default=[])
    parser.add_argument("--output", type=Path, default=Path("evidence/finops-report.json"))
    args = parser.parse_args()
    value = json.loads(args.evidence.read_text(encoding="utf-8"))
    report = evaluate_value_realization(value, approvals=set(args.approval))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"economics": report["economics"], "status": report["verification_status"]}, indent=2))


if __name__ == "__main__":
    main()
