"""One-case operator commands for the Azure backup resolution pilot."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .production_resolution import (
    attach_drill,
    case_report,
    diagnose_case,
    execute_retry,
    open_case,
    verify_case,
)


def _read(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"{path} must be a JSON object")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description="Production Resolution Operator pilot")
    parser.add_argument("--db", type=Path, default=Path("evidence/resolution-operator.sqlite3"))
    sub = parser.add_subparsers(dest="command", required=True)
    opened = sub.add_parser("open", help="Ingest an Azure Monitor common-schema alert export")
    opened.add_argument("--recipe", type=Path, required=True)
    opened.add_argument("--alert", type=Path, required=True)
    opened.add_argument("--scope", type=Path, required=True)
    diagnosis = sub.add_parser("diagnose", help="Collect or import read-only backup evidence")
    diagnosis.add_argument("case_id")
    diagnosis.add_argument("--snapshot", type=Path, help="Offline replay; omit for Azure CLI read")
    diagnosis.add_argument("--acknowledge-authorized-read", action="store_true")
    retry = sub.add_parser("retry", help="One reviewed, guarded Azure backup-now write")
    retry.add_argument("case_id")
    retry.add_argument("--approval", action="append", default=[])
    retry.add_argument("--cause-reviewed", action="store_true")
    retry.add_argument("--acknowledge-cloud-write", action="store_true")
    verification = sub.add_parser("verify", help="Collect or import post-retry evidence")
    verification.add_argument("case_id")
    verification.add_argument("--after", type=Path, help="Offline replay; omit for Azure CLI read")
    verification.add_argument("--acknowledge-authorized-read", action="store_true")
    drill = sub.add_parser("attach-drill", help="Bind a drill to the newly verified recovery point")
    drill.add_argument("case_id")
    drill.add_argument("--contract", type=Path, required=True)
    drill.add_argument("--restore", type=Path, required=True)
    drill.add_argument("--probe", type=Path, required=True)
    drill.add_argument("--pilot-id", help="Optional active Service Delivery Cloud pilot")
    show = sub.add_parser("show", help="Show the operator case summary")
    show.add_argument("case_id")
    args = parser.parse_args()
    if args.command == "open":
        case = open_case(args.db, _read(args.recipe), _read(args.alert), _read(args.scope))
        result = case_report(args.db, case["case_id"])
    elif args.command == "diagnose":
        if args.snapshot is None and not args.acknowledge_authorized_read:
            parser.error("--acknowledge-authorized-read is required for Azure CLI collection")
        diagnose_case(args.db, args.case_id, snapshot=_read(args.snapshot) if args.snapshot else None)
        result = case_report(args.db, args.case_id)
    elif args.command == "retry":
        execute_retry(args.db, args.case_id, approvals=set(args.approval),
                      cause_reviewed=args.cause_reviewed,
                      acknowledge_cloud_write=args.acknowledge_cloud_write)
        result = case_report(args.db, args.case_id)
    elif args.command == "verify":
        if args.after is None and not args.acknowledge_authorized_read:
            parser.error("--acknowledge-authorized-read is required for Azure CLI collection")
        verify_case(args.db, args.case_id, after=_read(args.after) if args.after else None)
        result = case_report(args.db, args.case_id)
    elif args.command == "attach-drill":
        attach_drill(args.db, args.case_id, _read(args.contract), _read(args.restore),
                     _read(args.probe), pilot_id=args.pilot_id)
        result = case_report(args.db, args.case_id)
    else:
        result = case_report(args.db, args.case_id)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
