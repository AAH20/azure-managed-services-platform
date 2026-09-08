from __future__ import annotations

import argparse
import json
from pathlib import Path

from .network_assurance import evaluate_network_change


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate a hybrid network change")
    parser.add_argument("evidence", type=Path)
    parser.add_argument("--approval", action="append", default=[])
    parser.add_argument("--output", type=Path, default=Path("evidence/network-report.json"))
    args = parser.parse_args()
    report = evaluate_network_change(
        json.loads(args.evidence.read_text(encoding="utf-8")), approvals=set(args.approval)
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"decision": report["decision"], "blockers": report["blockers"],
                      **report["economics"]}, indent=2))


if __name__ == "__main__":
    main()
