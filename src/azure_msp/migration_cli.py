from __future__ import annotations

import argparse
import json
from pathlib import Path

from .migration import evaluate_migration


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate an Azure migration wave")
    parser.add_argument("evidence", type=Path)
    parser.add_argument("--approval", action="append", default=[])
    parser.add_argument("--output", type=Path, default=Path("evidence/migration-report.json"))
    args = parser.parse_args()
    report = evaluate_migration(
        json.loads(args.evidence.read_text(encoding="utf-8")), approvals=set(args.approval)
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "migration_id": report["migration_id"],
                "wave_status": report["wave_status"],
                "dependency_order": report["dependency_order"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
