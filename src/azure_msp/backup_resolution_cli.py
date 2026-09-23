"""Read-only backup diagnosis and explicitly approved Azure VM backup retry."""

from __future__ import annotations

import argparse
import json
from hashlib import sha256
from pathlib import Path

from .azure_evidence import AzureCliRunner
from .backup_resolution import (
    BackupResolutionError,
    collect_snapshot,
    propose,
    retry_backup,
    validate_scope,
    verify,
)


def _read(path: Path) -> dict:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise BackupResolutionError(f"{path} must contain a JSON object")
    return value


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Azure VM backup resolution workcell")
    sub = parser.add_subparsers(dest="command", required=True)
    collect = sub.add_parser("collect")
    collect.add_argument("--scope", type=Path, required=True)
    collect.add_argument("--output", type=Path, required=True)
    collect.add_argument("--acknowledge-authorized-read", action="store_true")
    plan = sub.add_parser("plan")
    plan.add_argument("--snapshot", type=Path, required=True)
    plan.add_argument("--output", type=Path, required=True)
    retry = sub.add_parser("retry")
    retry.add_argument("--scope", type=Path, required=True)
    retry.add_argument("--before", type=Path, required=True)
    retry.add_argument("--plan", type=Path, required=True)
    retry.add_argument("--approval", action="append", default=[])
    retry.add_argument("--cause-reviewed", action="store_true")
    retry.add_argument("--acknowledge-cloud-write", action="store_true")
    retry.add_argument("--output", type=Path, required=True)
    check = sub.add_parser("verify")
    check.add_argument("--before", type=Path, required=True)
    check.add_argument("--after", type=Path, required=True)
    check.add_argument("--attempt", type=Path, required=True)
    check.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "collect":
            if not args.acknowledge_authorized_read:
                raise BackupResolutionError("--acknowledge-authorized-read is required")
            result = collect_snapshot(validate_scope(_read(args.scope)))
        elif args.command == "plan":
            result = propose(_read(args.snapshot))
        elif args.command == "retry":
            if not args.acknowledge_cloud_write or not args.cause_reviewed:
                raise BackupResolutionError(
                    "--cause-reviewed and --acknowledge-cloud-write are required")
            scope = validate_scope(_read(args.scope))
            before = _read(args.before)
            plan_value = _read(args.plan)
            digest = sha256(json.dumps(before, sort_keys=True).encode()).hexdigest()
            if plan_value.get("snapshot_sha256") != digest:
                raise BackupResolutionError("plan is not bound to the supplied before snapshot")
            current = collect_snapshot(scope)
            result = retry_backup(scope, plan_value, current, set(args.approval), AzureCliRunner())
        else:
            result = verify(_read(args.before), _read(args.after), _read(args.attempt))
        _write(args.output, result)
        print(json.dumps(result, indent=2))
        return 0
    except (OSError, json.JSONDecodeError, BackupResolutionError) as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
