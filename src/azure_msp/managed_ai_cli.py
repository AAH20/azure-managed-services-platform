"""One-shot operator commands for a managed inference service."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .managed_ai import ServiceError, monthly_report, probe, record, render_html, validate_service


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe and report a managed AI endpoint")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("probe", "report"):
        action = sub.add_parser(name)
        action.add_argument("service", type=Path)
        action.add_argument("--db", type=Path, required=True)
        if name == "probe":
            action.add_argument("--timeout", type=float, default=30)
        else:
            action.add_argument("--month", required=True)
            action.add_argument("--json-output", type=Path)
            action.add_argument("--html-output", type=Path)
    args = parser.parse_args()
    try:
        service = validate_service(json.loads(args.service.read_text()))
        if args.command == "probe":
            sample = probe(service, timeout_s=args.timeout,
                           api_key=os.environ.get("AZURE_MSP_ENDPOINT_TOKEN"))
            record(args.db, sample)
            print(json.dumps(sample, indent=2))
            return 0 if sample["ok"] else 1
        report = monthly_report(args.db, service, args.month)
        rendered = json.dumps(report, indent=2) + "\n"
        if args.json_output:
            args.json_output.parent.mkdir(parents=True, exist_ok=True)
            args.json_output.write_text(rendered)
        if args.html_output:
            args.html_output.parent.mkdir(parents=True, exist_ok=True)
            args.html_output.write_text(render_html(report))
        print(rendered, end="")
        return 0
    except (OSError, json.JSONDecodeError, ServiceError) as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
