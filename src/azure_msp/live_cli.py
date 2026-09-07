from __future__ import annotations

import argparse
import json
from pathlib import Path

from .azure_evidence import collect_baseline


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect a read-only Azure assessment baseline")
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--subscription-id", required=True)
    parser.add_argument("--output", type=Path, default=Path("evidence/live-baseline.json"))
    parser.add_argument(
        "--acknowledge-authorized-read",
        action="store_true",
        help="Confirm you are authorized to read the specified Azure subscription.",
    )
    args = parser.parse_args()
    if not args.acknowledge_authorized_read:
        parser.error("live collection requires --acknowledge-authorized-read")
    observations = collect_baseline(args.tenant_id, args.subscription_id)
    report = {
        "schema_version": "1.0",
        "mode": "read-only",
        "tenant_id": args.tenant_id,
        "subscription_id": args.subscription_id,
        "observations": [observation.to_dict() for observation in observations],
        "summary": {
            "sources_attempted": len(observations),
            "sources_observed": sum(item.status == "observed" for item in observations),
            "sources_unknown": sum(item.status == "unknown" for item in observations),
            "cloud_mutations_executed": 0,
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], indent=2))


if __name__ == "__main__":
    main()
