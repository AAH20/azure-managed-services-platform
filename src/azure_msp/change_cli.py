from __future__ import annotations

import argparse
import json
from pathlib import Path

from .change_assurance import ChangeContract, RingObservation, evaluate_rollout


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate a progressive infrastructure rollout")
    parser.add_argument("contract", type=Path)
    parser.add_argument("observations", type=Path)
    parser.add_argument("--approval", action="append", default=[])
    parser.add_argument("--output", type=Path, default=Path("evidence/change-report.json"))
    args = parser.parse_args()
    contract = ChangeContract.from_dict(json.loads(args.contract.read_text(encoding="utf-8")))
    observations = [
        RingObservation.from_dict(item)
        for item in json.loads(args.observations.read_text(encoding="utf-8"))["observations"]
    ]
    report = evaluate_rollout(contract, observations, approvals=set(args.approval))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "outcome": report["outcome"],
                "halted_at": report["halted_at"],
                **report["economics"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
