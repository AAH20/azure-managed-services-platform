from __future__ import annotations

import argparse
import json
from pathlib import Path

from .ai_value import evaluate_ai_value


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate AI unit economics and routing evidence")
    parser.add_argument("evidence", type=Path)
    parser.add_argument("--approval", action="append", default=[])
    parser.add_argument("--output", type=Path, default=Path("evidence/ai-value-report.json"))
    args = parser.parse_args()
    report = evaluate_ai_value(
        json.loads(args.evidence.read_text(encoding="utf-8")), approvals=set(args.approval)
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "total_cost_usd": report["economics"]["total_cost_usd"],
                "retry_and_rejected_waste_pct": report["economics"][
                    "retry_and_rejected_waste_pct"
                ],
                "selected_shadow_candidate_id": report["selected_shadow_candidate_id"],
                "routing_change_executed": report["routing_change_executed"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
