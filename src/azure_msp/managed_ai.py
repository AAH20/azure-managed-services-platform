"""Read-only inference service probes and local monthly operations reporting."""

from __future__ import annotations

import ipaddress
import json
import math
import re
import sqlite3
import time
from datetime import UTC, datetime
from html import escape
from pathlib import Path
from urllib import error, parse, request
from uuid import uuid4

SERVICE_ID = re.compile(r"^[a-z][a-z0-9-]{1,62}$")
MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


class ServiceError(ValueError):
    pass


def validate_service(value: dict) -> dict:
    if not isinstance(value, dict):
        raise ServiceError("service definition must be an object")
    required = {"service_id", "customer", "base_url", "model", "owner",
                "minimum_samples_per_month",
                "minimum_sample_success_pct", "maximum_p95_latency_ms", "maximum_p95_ttft_ms"}
    if required - value.keys():
        raise ServiceError(f"missing service fields: {sorted(required - value.keys())}")
    if not isinstance(value["service_id"], str) or not SERVICE_ID.fullmatch(value["service_id"]):
        raise ServiceError("invalid service_id")
    for key in ("customer", "model", "owner"):
        if not isinstance(value[key], str) or not value[key].strip() or len(value[key]) > 160:
            raise ServiceError(f"invalid {key}")
    if not isinstance(value["base_url"], str):
        raise ServiceError("base_url must be a URL")
    url = parse.urlsplit(value["base_url"])
    if url.username or url.password or url.query or url.fragment or url.path not in ("", "/"):
        raise ServiceError("base_url must contain only scheme, host and optional port")
    if not url.hostname:
        raise ServiceError("base_url host is required")
    if url.scheme != "https":
        try:
            is_loopback = ipaddress.ip_address(url.hostname).is_loopback
        except ValueError:
            is_loopback = url.hostname == "localhost"
        if url.scheme != "http" or not is_loopback:
            raise ServiceError("HTTPS is required except for loopback development")
    thresholds = {}
    for key in ("minimum_sample_success_pct", "maximum_p95_latency_ms", "maximum_p95_ttft_ms"):
        raw = value[key]
        if not isinstance(raw, (float, int)) or not math.isfinite(raw) or raw <= 0:
            raise ServiceError(f"invalid {key}")
        thresholds[key] = float(raw)
    if thresholds["minimum_sample_success_pct"] > 100:
        raise ServiceError("sample success target cannot exceed 100 percent")
    minimum_samples = value["minimum_samples_per_month"]
    if type(minimum_samples) is not int or minimum_samples < 1:
        raise ServiceError("minimum_samples_per_month must be a positive integer")
    return {**value, **thresholds, "base_url": value["base_url"].rstrip("/")}


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def probe(service: dict, *, timeout_s: float = 30, api_key: str | None = None) -> dict:
    service = validate_service(service)
    if timeout_s <= 0:
        raise ServiceError("timeout_s must be positive")
    opener = request.build_opener(_NoRedirect())
    started = time.monotonic()
    first_token = None
    output_seen = False
    result = {"sample_id": str(uuid4()), "service_id": service["service_id"],
              "observed_at": datetime.now(UTC).isoformat(), "ok": False,
              "latency_ms": None, "ttft_ms": None, "error": None}
    try:
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        health = request.Request(service["base_url"] + "/health", headers=headers)
        with opener.open(health, timeout=timeout_s) as response:
            if response.status != 200:
                raise ServiceError("health status is not 200")
        payload = json.dumps({"model": service["model"], "messages": [
            {"role": "user", "content": "Reply with the single word: ready"}],
            "max_tokens": 16, "temperature": 0, "stream": True}).encode()
        headers["Content-Type"] = "application/json"
        req = request.Request(service["base_url"] + "/v1/chat/completions", payload, headers)
        with opener.open(req, timeout=timeout_s) as response:
            if response.status != 200:
                raise ServiceError("inference status is not 200")
            for raw in response:
                if not raw.startswith(b"data: "):
                    continue
                data = raw[6:].strip()
                if data == b"[DONE]":
                    break
                item = json.loads(data)
                if "error" in item:
                    raise ServiceError("inference stream returned an error")
                if any(choice.get("delta", {}).get("content") for choice in item.get("choices", [])):
                    output_seen = True
                    if first_token is None:
                        first_token = (time.monotonic() - started) * 1000
        if not output_seen:
            raise ServiceError("inference returned no output token")
        result["ok"] = True
        result["latency_ms"] = round((time.monotonic() - started) * 1000, 2)
        result["ttft_ms"] = round(first_token, 2)
    except (error.URLError, OSError, TimeoutError, ValueError) as exc:
        result["error"] = type(exc).__name__
    return result


def record(db_path: Path, sample: dict) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS probes (
            sample_id TEXT PRIMARY KEY, service_id TEXT NOT NULL, observed_at TEXT NOT NULL,
            ok INTEGER NOT NULL, latency_ms REAL, ttft_ms REAL, error TEXT
        )""")
        conn.execute("INSERT INTO probes VALUES (?, ?, ?, ?, ?, ?, ?)", (
            sample["sample_id"], sample["service_id"], sample["observed_at"],
            int(sample["ok"]), sample["latency_ms"], sample["ttft_ms"], sample["error"],
        ))


def _p95(values: list[float]) -> float | None:
    if not values:
        return None
    values = sorted(values)
    position = (len(values) - 1) * 0.95
    low = math.floor(position)
    return round(values[low] + (values[math.ceil(position)] - values[low]) * (position - low), 2)


def monthly_report(db_path: Path, service: dict, month: str) -> dict:
    service = validate_service(service)
    if not MONTH.fullmatch(month):
        raise ServiceError("month must be YYYY-MM")
    if not db_path.exists():
        rows = []
    else:
        with sqlite3.connect(db_path) as conn:
            rows = conn.execute(
                "SELECT observed_at, ok, latency_ms, ttft_ms, error FROM probes "
                "WHERE service_id = ? AND observed_at >= ? AND observed_at < ? ORDER BY observed_at",
                (service["service_id"], month + "-01", _next_month(month) + "-01"),
            ).fetchall()
    successes = [row for row in rows if row[1]]
    rate = round(len(successes) / len(rows) * 100, 2) if rows else None
    latency = _p95([row[2] for row in successes])
    ttft = _p95([row[3] for row in successes])
    gates = {
        "sample_coverage": len(rows) >= service["minimum_samples_per_month"],
        "sample_success": rate is not None and rate >= service["minimum_sample_success_pct"],
        "probe_latency": latency is not None and latency <= service["maximum_p95_latency_ms"],
        "probe_ttft": ttft is not None and ttft <= service["maximum_p95_ttft_ms"],
    }
    return {
        "schema_version": "1.0", "service_id": service["service_id"],
        "customer": service["customer"], "owner": service["owner"], "month": month,
        "samples": len(rows), "successful_samples": len(successes),
        "sample_success_pct": rate, "p95_probe_latency_ms": latency,
        "p95_probe_ttft_ms": ttft, "gates": gates,
        "targets": {
            "minimum_samples_per_month": service["minimum_samples_per_month"],
            "minimum_sample_success_pct": service["minimum_sample_success_pct"],
            "maximum_p95_latency_ms": service["maximum_p95_latency_ms"],
            "maximum_p95_ttft_ms": service["maximum_p95_ttft_ms"],
        },
        "status": "unknown" if not rows else "insufficient_data" if not gates["sample_coverage"]
                  else "met" if all(gates.values()) else "attention",
        "error_types": sorted({row[4] for row in rows if row[4]}),
        "boundary": "Active synthetic inference probes only; sample success is not continuous availability or a contractual SLA. No customer traffic, GPU utilization, billing or answer quality measured.",
    }


def _next_month(month: str) -> str:
    year, number = map(int, month.split("-"))
    return f"{year + (number == 12):04d}-{number % 12 + 1:02d}"


def render_html(report: dict) -> str:
    def cell(value: object) -> str:
        return "—" if value is None else escape(str(value))

    metrics = (
        ("Samples", report["samples"]),
        ("Successful probes", report["successful_samples"]),
        ("Sample success", f"{report['sample_success_pct']}%" if report["sample_success_pct"] is not None else None),
        ("P95 probe latency", f"{report['p95_probe_latency_ms']} ms" if report["p95_probe_latency_ms"] is not None else None),
        ("P95 time to first token", f"{report['p95_probe_ttft_ms']} ms" if report["p95_probe_ttft_ms"] is not None else None),
    )
    cards = "".join(f"<div class='card'><span>{escape(label)}</span><strong>{cell(value)}</strong></div>"
                    for label, value in metrics)
    target_names = {
        "sample_coverage": "minimum_samples_per_month",
        "sample_success": "minimum_sample_success_pct",
        "probe_latency": "maximum_p95_latency_ms",
        "probe_ttft": "maximum_p95_ttft_ms",
    }
    gates = "".join(
        f"<tr><td>{escape(name.replace('_', ' '))}</td>"
        f"<td>{cell(report['targets'][target_names[name]])}</td>"
        f"<td>{'Met' if passed else 'Needs review'}</td></tr>"
        for name, passed in report["gates"].items()
    )
    return f"""<!doctype html><html lang='en'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Managed AI service — {escape(report['service_id'])}</title>
<style>body{{font:16px system-ui;background:#0b1220;color:#f8fafc;margin:0}}main{{max-width:1050px;margin:auto;padding:40px 22px}}
h1{{margin-bottom:4px}}p,span,small{{color:#cbd5e1}}.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin:28px 0}}
.card,section{{background:#172338;border:1px solid #6b7f9f;border-radius:10px;padding:18px}}.card span,.card strong{{display:block}}
.card strong{{font-size:24px;color:white;margin-top:8px}}table{{width:100%;border-collapse:collapse}}td{{padding:10px;border-bottom:1px solid #6b7f9f}}
.status{{display:inline-block;padding:5px 12px;border-radius:20px;background:#223e59;color:#f8fafc}}</style></head><body><main>
<h1>Managed AI service</h1><p>{escape(report['customer'])} · {escape(report['service_id'])} · {escape(report['month'])}</p>
<p>Owner: {escape(report['owner'])} · <span class='status'>{escape(report['status'])}</span></p>
<div class='cards'>{cards}</div><section><h2>Operating targets</h2><table><tr><th>Check</th><th>Target</th><th>Result</th></tr>{gates}</table></section>
<p><small>{escape(report['boundary'])}</small></p></main></body></html>"""
