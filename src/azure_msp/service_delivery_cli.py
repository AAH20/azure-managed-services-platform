"""Operator CLI for the local service-delivery pilot."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .service_delivery import (
    activate,
    customer_report,
    list_requests,
    onboard,
    operator_statement,
    record_work,
    request_pilot,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Local Azure VM backup service-delivery pilot")
    parser.add_argument("--db", type=Path, default=Path("evidence/service-delivery.sqlite3"))
    sub = parser.add_subparsers(dest="command", required=True)
    request = sub.add_parser("request", help="Submit a partner pilot request")
    request.add_argument("partner_id")
    request.add_argument("customer_id")
    request.add_argument("--vm-count", type=int, default=1)
    listing = sub.add_parser("requests", help="List partner-scoped requests")
    listing.add_argument("partner_id")
    onboard_cmd = sub.add_parser("onboard", help="Bind a signed contract and exact VM scope")
    onboard_cmd.add_argument("request_id")
    onboard_cmd.add_argument("scope_json", type=Path)
    onboard_cmd.add_argument("--contract-ref", required=True)
    onboard_cmd.add_argument("--monthly-fee-usd", required=True)
    onboard_cmd.add_argument("--hourly-cost-usd", required=True)
    activate_cmd = sub.add_parser("activate", help="Activate after observed backup evidence")
    activate_cmd.add_argument("pilot_id")
    activate_cmd.add_argument("snapshot_json", type=Path)
    work = sub.add_parser("work", help="Record operator labor against an in-scope case")
    work.add_argument("pilot_id")
    work.add_argument("case_id")
    work.add_argument("--minutes", type=int, required=True)
    work.add_argument("--note", required=True)
    report = sub.add_parser("report", help="Customer-safe JSON monthly report")
    report.add_argument("pilot_id")
    report.add_argument("month")
    statement = sub.add_parser("statement", help="Operator-only draft economics")
    statement.add_argument("pilot_id")
    statement.add_argument("month")
    args = parser.parse_args()
    if args.command in ("request", "requests"):
        token = os.environ["AZURE_MSP_PARTNER_TOKEN"]
        result = (request_pilot(args.db, args.partner_id, token, args.customer_id, args.vm_count)
                  if args.command == "request" else list_requests(args.db, args.partner_id, token))
    elif args.command == "onboard":
        result = onboard(args.db, args.request_id, json.loads(args.scope_json.read_text()),
                         args.contract_ref, args.monthly_fee_usd, args.hourly_cost_usd,
                         os.environ["AZURE_MSP_CUSTOMER_TOKEN"])
    elif args.command == "activate":
        result = activate(args.db, args.pilot_id, json.loads(args.snapshot_json.read_text()))
    elif args.command == "work":
        result = record_work(args.db, args.pilot_id, args.case_id, args.minutes, args.note)
    elif args.command == "report":
        result = customer_report(args.db, args.pilot_id, os.environ["AZURE_MSP_CUSTOMER_TOKEN"],
                                 args.month)
    else:
        result = operator_statement(args.db, args.pilot_id, args.month)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
