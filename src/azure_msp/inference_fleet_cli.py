"""Plan, review, and explicitly apply one order-bound inference workload."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from .inference_fleet import FleetError, deployment_plan, evaluate_release, validate_spec
from .partner_cloud import get_order


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="One-GPU inference workload operator")
    sub = parser.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("plan")
    plan.add_argument("--db", type=Path, required=True)
    plan.add_argument("--partner-id", required=True)
    plan.add_argument("--order-id", required=True)
    plan.add_argument("--spec", type=Path, required=True)
    plan.add_argument("--output", type=Path, required=True)
    evaluate = sub.add_parser("evaluate")
    evaluate.add_argument("--evidence", type=Path, required=True)
    evaluate.add_argument("--output", type=Path, required=True)
    apply = sub.add_parser("apply")
    apply.add_argument("--plan", type=Path, required=True)
    apply.add_argument("--namespace", required=True)
    apply.add_argument("--context", required=True)
    apply.add_argument("--acknowledge-cluster-write", action="store_true")
    rollback = sub.add_parser("rollback")
    rollback.add_argument("--decision", type=Path, required=True)
    rollback.add_argument("--namespace", required=True)
    rollback.add_argument("--context", required=True)
    rollback.add_argument("--acknowledge-cluster-write", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "plan":
            order = get_order(args.db, args.partner_id, args.order_id)
            if order is None:
                raise FleetError("order not found")
            spec = validate_spec(json.loads(args.spec.read_text()))
            result = deployment_plan(order, spec)
            _write(args.output, result)
            print(json.dumps({"plan": str(args.output), "resources": len(result["items"])}))
        elif args.command == "evaluate":
            result = evaluate_release(json.loads(args.evidence.read_text()))
            _write(args.output, result)
            print(json.dumps({"decision": result["decision"], "output": str(args.output)}))
        elif args.command == "apply":
            if not args.acknowledge_cluster_write:
                raise FleetError("--acknowledge-cluster-write is required")
            result = json.loads(args.plan.read_text())
            if result.get("kind") != "List" or len(result.get("items", [])) != 2:
                raise FleetError("plan must contain exactly a Deployment and Service")
            deployment, service = result["items"]
            if deployment.get("kind") != "Deployment" or service.get("kind") != "Service":
                raise FleetError("plan must contain a Deployment and Service")
            namespace = deployment["metadata"]["namespace"]
            if service["metadata"]["namespace"] != namespace or namespace != args.namespace:
                raise FleetError("resource namespace mismatch")
            subprocess.run(["kubectl", "--context", args.context, "--namespace", namespace,
                            "apply", "-f", "-"], input=json.dumps(result), text=True, check=True)
        else:
            if not args.acknowledge_cluster_write:
                raise FleetError("--acknowledge-cluster-write is required")
            result = json.loads(args.decision.read_text())
            if result.get("decision") != "rollback_required":
                raise FleetError("decision does not require rollback")
            order_id = result.get("order_id")
            if not isinstance(order_id, str) or not order_id.startswith("ord-") or not order_id[4:].isalnum():
                raise FleetError("invalid order_id in decision")
            if not args.namespace.islower() or not args.namespace.replace("-", "").isalnum():
                raise FleetError("invalid namespace")
            subprocess.run(["kubectl", "--context", args.context, "--namespace", args.namespace,
                            "rollout", "undo", f"deployment/if-{order_id}"], check=True)
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
