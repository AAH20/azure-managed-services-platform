"""Loopback single-customer alert receiver and local queue worker."""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import secrets
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .customer_gateway import (
    GatewayError,
    enqueue,
    queue_status,
    sign_payload,
    validate_config,
    work_once,
)


class GatewayHandler(BaseHTTPRequestHandler):
    db: Path
    config: dict
    key: str

    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send(200, {"status": "ok"})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path != "/v1/alerts":
            self._send(404, {"error": "not found"})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 65536:
                raise GatewayError("invalid body size")
            result = enqueue(self.db, self.config, self.rfile.read(size),
                             timestamp=int(self.headers.get("X-A2Z-Timestamp", "0")),
                             nonce=self.headers.get("X-A2Z-Nonce", ""),
                             signature=self.headers.get("X-A2Z-Signature", ""), key=self.key)
        except (GatewayError, ValueError):
            self._send(401, {"error": "alert rejected"})
            return
        self._send(202, result)

    def log_message(self, format, *args):
        # Do not write customer identifiers, signatures, headers, or alert bodies to stdout.
        pass


def serve(db: Path, config: dict, key: str, host: str, port: int) -> None:
    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = False
    if not loopback or not 1 <= port <= 65535:
        raise GatewayError("built-in server requires a loopback IP and valid port")
    sign_payload(key, int(time.time()), secrets.token_hex(16), b"check")
    handler = type("ConfiguredGatewayHandler", (GatewayHandler,),
                   {"db": db, "config": validate_config(config), "key": key})
    with ThreadingHTTPServer((host, port), handler) as server:
        print(f"Gateway listening on {host}:{port}")
        server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description="Single-customer local operations gateway")
    parser.add_argument("--db", type=Path, default=Path("evidence/customer-gateway.sqlite3"))
    parser.add_argument("--config", type=Path, required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    server = sub.add_parser("serve", help="Run HMAC-authenticated loopback alert receiver")
    server.add_argument("--host", default="127.0.0.1")
    server.add_argument("--port", type=int, default=8173)
    replay = sub.add_parser("enqueue-file", help="Locally replay an alert through intake checks")
    replay.add_argument("alert", type=Path)
    sub.add_parser("work-once", help="Lease one alert and open an idempotent case")
    sub.add_parser("status", help="Show local queue counts")
    args = parser.parse_args()
    config = validate_config(json.loads(args.config.read_text(encoding="utf-8")))
    if args.command == "serve":
        serve(args.db, config, os.environ["AZURE_MSP_GATEWAY_HMAC_KEY"], args.host, args.port)
        return
    if args.command == "enqueue-file":
        key = os.environ["AZURE_MSP_GATEWAY_HMAC_KEY"]
        body = args.alert.read_bytes()
        timestamp, nonce = int(time.time()), secrets.token_hex(16)
        result = enqueue(args.db, config, body, timestamp=timestamp, nonce=nonce,
                         signature=sign_payload(key, timestamp, nonce, body), key=key)
    elif args.command == "work-once":
        result = work_once(args.db, config)
    else:
        result = queue_status(args.db, config)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
