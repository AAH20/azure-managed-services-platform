from __future__ import annotations

import argparse
import json
from pathlib import Path

from .recovery import DrillEvent, RecoveryContract, evaluate_drill, validate_drill_authority


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay and evaluate recovery drill evidence")
    parser.add_argument("contract", type=Path)
    parser.add_argument("events", type=Path)
    parser.add_argument("--approval", action="append", default=[])
    parser.add_argument("--isolated-network", action="store_true")
    parser.add_argument("--production-failover", action="store_true")
    parser.add_argument("--output", type=Path, default=Path("evidence/recovery-report.json"))
    args = parser.parse_args()
    contract = RecoveryContract.from_dict(json.loads(args.contract.read_text(encoding="utf-8")))
    validate_drill_authority(
        contract,
        isolated_network=args.isolated_network,
        requested_production_failover=args.production_failover,
        approvals=set(args.approval),
    )
    events = [
        DrillEvent.from_dict(item)
        for item in json.loads(args.events.read_text(encoding="utf-8"))["events"]
    ]
    report = evaluate_drill(contract, events)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"outcome": report["outcome"], **report["observed"]}, indent=2))


if __name__ == "__main__":
    main()
