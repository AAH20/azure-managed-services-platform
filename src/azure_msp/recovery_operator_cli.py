"""Probe an isolated application, then evaluate after cleanup is confirmed."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .recovery_operator import (
    evaluate,
    list_followups,
    probe_application,
    record_result,
    validate_contract,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Application-level recovery drill operator")
    sub = parser.add_subparsers(dest="command", required=True)
    probe_cmd = sub.add_parser("probe", help="Perform one opt-in read-only HTTPS GET")
    probe_cmd.add_argument("contract", type=Path)
    probe_cmd.add_argument("--acknowledge-authorized-probe", action="store_true")
    probe_cmd.add_argument("--output", type=Path, default=Path("evidence/recovery-probe.json"))
    evaluate_cmd = sub.add_parser("evaluate", help="Evaluate restore, probe and cleanup evidence")
    evaluate_cmd.add_argument("contract", type=Path)
    evaluate_cmd.add_argument("restore_evidence", type=Path)
    evaluate_cmd.add_argument("probe_result", type=Path)
    evaluate_cmd.add_argument("--db", type=Path, help="Optional Service Delivery Cloud SQLite file")
    evaluate_cmd.add_argument("--pilot-id", help="Required with --db")
    evaluate_cmd.add_argument("--output", type=Path,
                              default=Path("evidence/recovery-operator-report.json"))
    followups = sub.add_parser("followups", help="List operator follow-ups for a pilot")
    followups.add_argument("pilot_id")
    followups.add_argument("--db", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "followups":
        print(json.dumps(list_followups(args.db, args.pilot_id), indent=2))
        return
    contract = validate_contract(json.loads(args.contract.read_text(encoding="utf-8")))
    if args.command == "probe":
        if not args.acknowledge_authorized_probe:
            parser.error("--acknowledge-authorized-probe is required for a live request")
        result = probe_application(contract)
    else:
        evidence = json.loads(args.restore_evidence.read_text(encoding="utf-8"))
        probe = json.loads(args.probe_result.read_text(encoding="utf-8"))
        result = evaluate(contract, evidence, probe)
        if bool(args.db) != bool(args.pilot_id):
            parser.error("--db and --pilot-id must be supplied together")
        if args.db:
            result["service_delivery"] = record_result(args.db, args.pilot_id, result)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.command == "probe":
        print(json.dumps({"passed": result["passed"], "reason": result["reason"]}, indent=2))
    else:
        print(json.dumps({"outcome": result["outcome"], "checks": result["checks"],
                          "actual_rto_seconds": result["actual_rto_seconds"],
                          "actual_rpo_seconds": result["actual_rpo_seconds"]}, indent=2))


if __name__ == "__main__":
    main()
