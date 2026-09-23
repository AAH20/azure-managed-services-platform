"""Evaluate paired inference service benchmarks and share partner scorecards."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .ai_service_operator import evaluate, partner_scores, plan_service, record_score


def main() -> None:
    parser = argparse.ArgumentParser(description="AI service endpoint operator")
    sub = parser.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("plan", help="Generate pinned one-GPU deployment plan for an order")
    plan.add_argument("--db", type=Path, required=True)
    plan.add_argument("--partner-id", required=True)
    plan.add_argument("--order-id", required=True)
    plan.add_argument("--spec", type=Path, required=True)
    plan.add_argument("--output", type=Path, required=True)
    score = sub.add_parser("evaluate", help="Evaluate paired hosted and BYOC endpoint evidence")
    score.add_argument("contract", type=Path)
    score.add_argument("evidence", type=Path)
    score.add_argument("--output", type=Path, default=Path("evidence/ai-service-score.json"))
    score.add_argument("--db", type=Path, help="Optional existing Partner Cloud order database")
    listing = sub.add_parser("scores", help="List scorecards for one authenticated partner order")
    listing.add_argument("--db", type=Path, required=True)
    listing.add_argument("--partner-id", required=True)
    listing.add_argument("--order-id", required=True)
    args = parser.parse_args()
    if args.command == "plan":
        result = plan_service(args.db, args.partner_id, args.order_id,
                              json.loads(args.spec.read_text(encoding="utf-8")))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps({"plan": str(args.output), "resources": len(result["items"])}))
        return
    if args.command == "scores":
        result = partner_scores(args.db, args.partner_id,
                                os.environ["AZURE_MSP_PARTNER_TOKEN"], args.order_id)
        print(json.dumps(result, indent=2, sort_keys=True))
        return
    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    evidence = json.loads(args.evidence.read_text(encoding="utf-8"))
    result = evaluate(contract, evidence)
    if args.db:
        result["partner_record"] = record_score(args.db, result)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"decision": result["decision"], "failed_gates": result["failed_gates"],
                      "candidate_cost_per_accepted_usd": result["candidate"]["cost_per_accepted_usd"]},
                     indent=2))


if __name__ == "__main__":
    main()
