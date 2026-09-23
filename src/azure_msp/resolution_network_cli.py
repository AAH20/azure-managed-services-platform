"""Local command-line runner for versioned resolution workcell cases."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .resolution_network import (
    NetworkError,
    diagnose,
    get_case,
    ingest,
    record_attempt,
    record_verification,
    verify_chain,
)


def _read(path: Path) -> dict:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise NetworkError(f"{path} must contain a JSON object")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="Local A2Z Resolution Network case runner")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("ingest", "diagnose", "record-attempt", "verify", "show"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--db", type=Path, required=True)
        if name == "ingest":
            cmd.add_argument("--recipe", type=Path, required=True)
            cmd.add_argument("--alert", type=Path, required=True)
            cmd.add_argument("--scope", type=Path, required=True)
        else:
            cmd.add_argument("--case-id", required=True)
        if name == "diagnose":
            cmd.add_argument("--snapshot", type=Path, required=True)
        if name == "record-attempt":
            cmd.add_argument("--attempt", type=Path, required=True)
        if name == "verify":
            cmd.add_argument("--after", type=Path, required=True)
        if name == "show":
            cmd.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "ingest":
            case = ingest(args.db, _read(args.recipe), _read(args.alert), _read(args.scope))
        elif args.command == "diagnose":
            case = diagnose(args.db, args.case_id, _read(args.snapshot))
        elif args.command == "record-attempt":
            case = record_attempt(args.db, args.case_id, _read(args.attempt))
        elif args.command == "verify":
            case = record_verification(args.db, args.case_id, _read(args.after))
        else:
            case = get_case(args.db, args.case_id)
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(json.dumps(case, indent=2) + "\n")
        print(json.dumps({"case_id": case["case_id"], "state": case["state"],
                          "workcell_id": case["workcell_id"],
                          "event_chain_valid": verify_chain(args.db, case["case_id"])}))
        return 0
    except (OSError, json.JSONDecodeError, NetworkError, ValueError) as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
