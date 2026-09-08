from __future__ import annotations

import argparse
import json
from pathlib import Path

from .capacity import evaluate_capacity_plan


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate a workload capacity plan")
    parser.add_argument("evidence", type=Path)
    parser.add_argument("--approval", action="append", default=[])
    parser.add_argument("--output", type=Path, default=Path("evidence/capacity-report.json"))
    args = parser.parse_args()
    report = evaluate_capacity_plan(
        json.loads(args.evidence.read_text(encoding="utf-8")), approvals=set(args.approval)
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"decision": report["decision"],
                      "selected_portfolio_id": report["selected_portfolio_id"],
                      "production_scaling_authorized": report["production_scaling_authorized"]},
                     indent=2))


if __name__ == "__main__":
    main()
