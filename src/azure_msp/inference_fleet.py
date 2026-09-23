"""Order-bound, single-GPU inference deployment planning and release evaluation."""

from __future__ import annotations

import math
import re
from decimal import Decimal, InvalidOperation

NAME = re.compile(r"^[a-z][a-z0-9-]{1,62}$")
SHA = re.compile(r"^[0-9a-f]{40}$")
IMAGE = re.compile(r"^[a-zA-Z0-9._/-]+@sha256:[0-9a-f]{64}$")


class FleetError(ValueError):
    """Invalid deployment intent or benchmark evidence."""


def _name(value: object, field: str) -> str:
    if not isinstance(value, str) or not NAME.fullmatch(value):
        raise FleetError(f"{field} must be a Kubernetes-safe lowercase name")
    return value


def validate_spec(value: dict) -> dict:
    if not isinstance(value, dict):
        raise FleetError("spec must be an object")
    for field in ("namespace", "cache_pvc", "hf_token_secret"):
        _name(value.get(field), field)
    image = value.get("image")
    if not isinstance(image, str) or not IMAGE.fullmatch(image):
        raise FleetError("image must be pinned by sha256 digest")
    revision = value.get("model_revision")
    if not isinstance(revision, str) or not SHA.fullmatch(revision):
        raise FleetError("model_revision must be a 40-character commit SHA")
    max_len = value.get("max_model_len")
    if type(max_len) is not int or not 256 <= max_len <= 32768:
        raise FleetError("max_model_len must be an integer from 256 to 32768")
    gpu = value.get("gpu_count")
    if type(gpu) is not int or gpu != 1:
        raise FleetError("this first release supports exactly one GPU")
    return value


def deployment_plan(order: dict, spec: dict) -> dict:
    """Produce Kubernetes JSON resources without contacting a cluster."""
    validate_spec(spec)
    if order.get("state") not in ("requested", "active"):
        raise FleetError("deployment planning requires a requested or active order")
    order_id = order.get("order_id")
    if not isinstance(order_id, str) or not NAME.fullmatch(order_id):
        raise FleetError("invalid order_id")
    if not isinstance(order.get("model"), str) or not order["model"].strip():
        raise FleetError("order model is missing")
    service = f"if-{order_id}"
    if len(service) > 63:
        raise FleetError("generated service name is too long")
    namespace = spec["namespace"]
    labels = {"app.kubernetes.io/name": "inference-fleet", "a2z.soc/order-id": order_id}
    container = {
        "name": "vllm", "image": spec["image"], "imagePullPolicy": "IfNotPresent",
        "args": ["--model", order["model"], "--revision", spec["model_revision"],
                 "--served-model-name", order_id, "--host", "0.0.0.0", "--port", "8000",
                 "--max-model-len", str(spec["max_model_len"])],
        "ports": [{"containerPort": 8000, "name": "http"}],
        "env": [{"name": "HF_TOKEN", "valueFrom": {"secretKeyRef": {
            "name": spec["hf_token_secret"], "key": "token"}}}],
        "resources": {"requests": {"nvidia.com/gpu": "1", "cpu": "2", "memory": "8Gi"},
                      "limits": {"nvidia.com/gpu": "1", "memory": "16Gi"}},
        "volumeMounts": [{"name": "model-cache", "mountPath": "/root/.cache/huggingface"}],
        "startupProbe": {"httpGet": {"path": "/health", "port": 8000},
                         "periodSeconds": 10, "failureThreshold": 120},
        "readinessProbe": {"httpGet": {"path": "/health", "port": 8000},
                           "periodSeconds": 10, "failureThreshold": 3},
    }
    deployment = {
        "apiVersion": "apps/v1", "kind": "Deployment",
        "metadata": {"name": service, "namespace": namespace, "labels": labels},
        "spec": {"replicas": 1, "revisionHistoryLimit": 2,
                 "strategy": {"type": "RollingUpdate", "rollingUpdate": {
                     "maxSurge": 1, "maxUnavailable": 0}},
                 "selector": {"matchLabels": labels},
                 "template": {"metadata": {"labels": labels}, "spec": {
                     "containers": [container],
                     "volumes": [{"name": "model-cache", "persistentVolumeClaim": {
                         "claimName": spec["cache_pvc"]}}],
                     "securityContext": {"runAsNonRoot": True, "runAsUser": 1000,
                                         "fsGroup": 1000}}}},
    }
    service_resource = {
        "apiVersion": "v1", "kind": "Service",
        "metadata": {"name": service, "namespace": namespace, "labels": labels},
        "spec": {"type": "ClusterIP", "selector": labels,
                 "ports": [{"name": "http", "port": 8000, "targetPort": 8000}]},
    }
    return {"apiVersion": "v1", "kind": "List", "items": [deployment, service_resource]}


def _number(value: object, key: str, *, positive: bool = False) -> float:
    if type(value) not in (int, float) or not math.isfinite(value):
        raise FleetError(f"{key} must be a finite number")
    if (positive and value <= 0) or (not positive and value < 0):
        raise FleetError(f"{key} must be {'positive' if positive else 'nonnegative'}")
    return float(value)


def _money(value: object, key: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError):
        raise FleetError(f"{key} must be money") from None
    if not result.is_finite() or result < 0:
        raise FleetError(f"{key} must be nonnegative money")
    return result


def evaluate_release(evidence: dict) -> dict:
    """Compare measured baseline and candidate; never claim cluster mutation."""
    if not isinstance(evidence, dict):
        raise FleetError("evidence must be an object")
    metrics = ("success_pct", "p95_latency_ms", "p95_ttft_ms", "gpu_hours")
    for label in ("baseline", "candidate"):
        item = evidence.get(label)
        if not isinstance(item, dict):
            raise FleetError(f"{label} must be an object")
        for key in ("requests", "output_tokens"):
            if type(item.get(key)) is not int or item[key] < 1:
                raise FleetError(f"{label}.{key} must be a positive integer")
        for key in metrics:
            _number(item.get(key), f"{label}.{key}", positive=key == "gpu_hours")
        _money(item.get("gpu_hour_usd"), f"{label}.gpu_hour_usd")
        if item["success_pct"] > 100:
            raise FleetError("success_pct cannot exceed 100")
        fingerprint = item.get("workload_sha256")
        if not isinstance(fingerprint, str) or not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
            raise FleetError(f"{label}.workload_sha256 must be a SHA-256 digest")
        _name(item.get("gpu_profile"), f"{label}.gpu_profile")
    if (evidence["baseline"]["workload_sha256"] != evidence["candidate"]["workload_sha256"]
            or evidence["baseline"]["gpu_profile"] != evidence["candidate"]["gpu_profile"]):
        raise FleetError("baseline and candidate must share workload and GPU profile")
    targets = evidence.get("targets")
    if not isinstance(targets, dict):
        raise FleetError("targets must be an object")
    for key in ("minimum_success_pct", "maximum_p95_latency_ms", "maximum_p95_ttft_ms",
                "maximum_cost_per_million_output_tokens_usd"):
        _number(targets.get(key), f"targets.{key}", positive=True)
    if targets["minimum_success_pct"] > 100:
        raise FleetError("minimum_success_pct cannot exceed 100")
    minimum_requests = targets.get("minimum_requests")
    if type(minimum_requests) is not int or minimum_requests < 1:
        raise FleetError("minimum_requests must be a positive integer")

    def cost(item: dict) -> Decimal:
        return _money(item["gpu_hour_usd"], "gpu_hour_usd") * Decimal(str(item["gpu_hours"])) * (
            Decimal(1_000_000) / Decimal(str(item["output_tokens"])))

    baseline_cost = cost(evidence["baseline"])
    candidate_cost = cost(evidence["candidate"])
    candidate = evidence["candidate"]
    failures = []
    gates = {
        "sample_size": candidate["requests"] >= minimum_requests,
        "success": candidate["success_pct"] >= targets["minimum_success_pct"],
        "latency": candidate["p95_latency_ms"] <= targets["maximum_p95_latency_ms"],
        "ttft": candidate["p95_ttft_ms"] <= targets["maximum_p95_ttft_ms"],
        "cost": candidate_cost <= Decimal(str(targets["maximum_cost_per_million_output_tokens_usd"])),
        "no_success_regression": candidate["success_pct"] >= evidence["baseline"]["success_pct"],
        "no_cost_regression": candidate_cost <= baseline_cost,
    }
    failures.extend(key for key, passed in gates.items() if not passed)
    return {
        "schema_version": "1.0", "order_id": _name(evidence.get("order_id"), "order_id"),
        "gates": gates, "failed_gates": failures,
        "baseline_cost_per_million_output_tokens_usd": str(baseline_cost.quantize(Decimal("0.01"))),
        "candidate_cost_per_million_output_tokens_usd": str(candidate_cost.quantize(Decimal("0.01"))),
        "decision": "eligible_for_operator_review" if not failures else "rollback_required",
        "cluster_mutations_executed": 0,
        "boundary": "Imported benchmark evidence; provenance and workload comparability require operator verification. This decision does not change cluster traffic or execute rollback.",
    }
