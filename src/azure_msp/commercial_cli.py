from __future__ import annotations

import argparse
import json
from pathlib import Path

from .commercial import evaluate_commercial_onboarding


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate commercial tenant onboarding")
    parser.add_argument("evidence", type=Path)
    parser.add_argument("--approval", action="append", default=[])
    parser.add_argument("--output", type=Path, default=Path("evidence/commercial-report.json"))
    args = parser.parse_args()
    report = evaluate_commercial_onboarding(
        json.loads(args.evidence.read_text(encoding="utf-8")), approvals=set(args.approval)
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "decision": report["decision"],
                "selected_offer_id": report["selected_offer_id"],
                "usage_reconciliation": report["usage_reconciliation"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
