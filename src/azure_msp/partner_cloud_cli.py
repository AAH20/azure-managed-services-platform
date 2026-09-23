"""Operator bootstrap and loopback partner ordering API."""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .partner_cloud import (
    PartnerError,
    activate_order,
    authenticate,
    create_order,
    get_order,
    list_orders,
    register_partner,
    service_definition,
    statement,
    validate_catalog,
)


class PartnerHandler(BaseHTTPRequestHandler):
    db: Path
    catalog: dict

    def _send(self, status: int, payload: dict | list) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _identity(self) -> str | None:
        partner_id = self.headers.get("X-Partner-ID", "")
        header = self.headers.get("Authorization", "")
        token = header[7:] if header.startswith("Bearer ") else ""
        if not authenticate(self.db, partner_id, token):
            self._send(401, {"error": "unauthorized"})
            return None
        return partner_id

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send(200, {"status": "ok"})
            return
        partner_id = self._identity()
        if partner_id is None:
            return
        path = urlsplit(self.path).path.rstrip("/").split("/")
        if path == ["", "v1", "catalog"]:
            prices = self.catalog["prices_cents"]
            self._send(200, {"sku": self.catalog["sku"], "model": self.catalog["model"],
                             "allowed_regions": self.catalog["allowed_regions"],
                             "operator_owner": self.catalog["operator_owner"],
                             "prices_usd": {key: f"{value / 100:.2f}" for key, value in prices.items()}})
        elif path == ["", "v1", "orders"]:
            self._send(200, list_orders(self.db, partner_id))
        elif len(path) == 4 and path[:3] == ["", "v1", "orders"]:
            order = get_order(self.db, partner_id, path[3])
            self._send(200, order) if order else self._send(404, {"error": "not found"})
        elif len(path) == 6 and path[:3] == ["", "v1", "orders"] and path[4] == "statements":
            try:
                self._send(200, statement(self.db, self.catalog, partner_id, path[3], path[5]))
            except PartnerError as exc:
                self._send(400, {"error": str(exc)})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self) -> None:
        partner_id = self._identity()
        if partner_id is None:
            return
        if self.path != "/v1/orders":
            self._send(404, {"error": "not found"})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 16384:
                raise PartnerError("order body size must be 1–16384 bytes")
            body = json.loads(self.rfile.read(size))
            self._send(201, create_order(self.db, self.catalog, partner_id, body))
        except (PartnerError, ValueError, json.JSONDecodeError) as exc:
            self._send(400, {"error": str(exc)})

    def log_message(self, format, *args):
        # Do not log authorization headers or customer request bodies.
        pass


def run_server(db: Path, catalog: dict, host: str, port: int) -> None:
    try:
        if not ipaddress.ip_address(host).is_loopback:
            raise PartnerError("the built-in HTTP server must bind to loopback")
    except ValueError as exc:
        raise PartnerError("host must be a loopback IP address") from exc
    if not 1 <= port <= 65535:
        raise PartnerError("invalid port")
    handler = type("ConfiguredPartnerHandler", (PartnerHandler,), {"db": db, "catalog": catalog})
    with ThreadingHTTPServer((host, port), handler) as server:
        print(f"Partner API listening on {host}:{port}")
        server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description="Single-SKU private AI partner API")
    sub = parser.add_subparsers(dest="command", required=True)
    register = sub.add_parser("register-partner")
    register.add_argument("--db", type=Path, required=True)
    register.add_argument("--partner-id", required=True)
    serve = sub.add_parser("serve")
    serve.add_argument("--db", type=Path, required=True)
    serve.add_argument("--catalog", type=Path, required=True)
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    activate = sub.add_parser("activate")
    activate.add_argument("--db", type=Path, required=True)
    activate.add_argument("--order-id", required=True)
    activate.add_argument("--deployment-ref", required=True)
    activate.add_argument("--contract-ref", required=True)
    activate.add_argument("--endpoint-url", required=True)
    config = sub.add_parser("service-config")
    config.add_argument("--db", type=Path, required=True)
    config.add_argument("--partner-id", required=True)
    config.add_argument("--order-id", required=True)
    config.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "register-partner":
            token = os.environ.get("PARTNER_CLOUD_BOOTSTRAP_TOKEN", "")
            register_partner(args.db, args.partner_id, token)
            print(json.dumps({"registered_partner": args.partner_id}))
        elif args.command == "serve":
            catalog = validate_catalog(json.loads(args.catalog.read_text()))
            run_server(args.db, catalog, args.host, args.port)
        elif args.command == "activate":
            order = activate_order(args.db, args.order_id, args.deployment_ref,
                                   args.contract_ref, args.endpoint_url)
            print(json.dumps({"order_id": order["order_id"], "state": order["state"]}))
        else:
            order = get_order(args.db, args.partner_id, args.order_id)
            if order is None:
                raise PartnerError("order not found")
            service = service_definition(order)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(service, indent=2) + "\n")
            print(json.dumps({"service_config": str(args.output)}))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
