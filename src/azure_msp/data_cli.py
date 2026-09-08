from __future__ import annotations

import argparse
import json
from pathlib import Path

from .data_reliability import evaluate_data_release


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate a governed data-product release")
    parser.add_argument("evidence", type=Path)
    parser.add_argument("--approval", action="append", default=[])
    parser.add_argument("--output", type=Path, default=Path("evidence/data-report.json"))
    args = parser.parse_args()
    report = evaluate_data_release(
        json.loads(args.evidence.read_text(encoding="utf-8")), approvals=set(args.approval)
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"decision": report["decision"],
                      "affected_consumers": report["affected_consumers"],
                      **report["economics"]}, indent=2))


if __name__ == "__main__":
    main()
