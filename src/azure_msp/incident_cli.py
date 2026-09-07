from __future__ import annotations

import argparse
import json
from pathlib import Path

from .incident import (
    DiagnosticEvidence,
    IncidentContract,
    IncidentSignal,
    RestorationObservation,
    evaluate_incident,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay an incident and verify restoration")
    parser.add_argument("contract", type=Path)
    parser.add_argument("evidence", type=Path)
    parser.add_argument("--action", required=True)
    parser.add_argument("--approval", action="append", default=[])
    parser.add_argument("--output", type=Path, default=Path("evidence/incident-report.json"))
    args = parser.parse_args()
    contract = IncidentContract.from_dict(json.loads(args.contract.read_text(encoding="utf-8")))
    payload = json.loads(args.evidence.read_text(encoding="utf-8"))
    report = evaluate_incident(
        contract,
        [IncidentSignal.from_dict(item) for item in payload["signals"]],
        [DiagnosticEvidence.from_dict(item) for item in payload["diagnostics"]],
        RestorationObservation.from_dict(payload["restoration"]),
        proposed_action=args.action,
        approvals=set(args.approval),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "service_restored": report["restoration"]["service_restored"],
                "restore_objective_met": report["restoration"]["restore_objective_met"],
                "estimated_margin_exposure_usd": report["economics"][
                    "estimated_contribution_margin_exposure_usd"
                ],
                "cloud_mutations_executed": report["cloud_mutations_executed"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
