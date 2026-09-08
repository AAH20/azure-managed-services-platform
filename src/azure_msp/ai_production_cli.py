from __future__ import annotations

import argparse
import json
from pathlib import Path

from .ai_production import evaluate_ai_release


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate a production AI release")
    parser.add_argument("evidence", type=Path)
    parser.add_argument("--approval", action="append", default=[])
    parser.add_argument("--output", type=Path, default=Path("evidence/ai-release-report.json"))
    args = parser.parse_args()
    report = evaluate_ai_release(
        json.loads(args.evidence.read_text(encoding="utf-8")), approvals=set(args.approval)
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "release_status": report["release_status"],
                "selected_candidate_id": report["selected_candidate_id"],
                "production_promotion_authorized": report["production_promotion_authorized"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
