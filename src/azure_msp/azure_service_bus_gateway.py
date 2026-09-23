"""Managed-identity Service Bus transport for one customer backup workcell.

The receiver requires a persistent, single-writer case database. Azure credentials
authorize access to the queue; they do not authorize a backup mutation.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Callable
from contextlib import closing
from hashlib import sha256
from pathlib import Path

from .customer_gateway import MAX_BODY, _bind_config, initialize, validate_config
from .production_resolution import open_case
from .resolution_network import NetworkError, parse_alert

NAMESPACE = re.compile(r"^[a-z0-9-]{6,50}\.servicebus\.windows\.net$")
QUEUE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,259}$")


class InvalidTransportMessage(ValueError):
    """A delivered message cannot be accepted for this customer's workcell."""


def validate_address(namespace: str, queue: str) -> tuple[str, str]:
    if (not isinstance(namespace, str) or not NAMESPACE.fullmatch(namespace)
            or not isinstance(queue, str) or not QUEUE.fullmatch(queue)):
        raise ValueError("expected a Service Bus namespace FQDN and queue name")
    return namespace, queue


def prepare_alert(config: dict, body: bytes) -> tuple[dict, str]:
    config = validate_config(config)
    if not isinstance(body, bytes) or not 0 < len(body) <= MAX_BODY:
        raise InvalidTransportMessage("alert body must be 1..65536 bytes")
    try:
        alert = json.loads(body)
        normalized = parse_alert(alert, config["scope"], config["recipe"])
    except (UnicodeDecodeError, json.JSONDecodeError, NetworkError) as exc:
        raise InvalidTransportMessage("invalid or out-of-scope alert") from exc
    # A stable, non-sensitive MessageId supports the broker's finite deduplication window.
    message_id = sha256((config["customer_id"] + "\0" + normalized["alert_id"]).encode()).hexdigest()
    return alert, message_id


def _initialize(db: Path, config: dict) -> None:
    initialize(db)
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""CREATE TABLE IF NOT EXISTS bus_alerts (
            alert_id TEXT PRIMARY KEY, payload_sha256 TEXT NOT NULL,
            message_id TEXT NOT NULL, case_id TEXT)""")
    _bind_config(db, config)


def open_delivered_case(db: Path, config: dict, body: bytes, message_id: str) -> dict:
    """Record the body digest before case creation, making crash replays safe."""
    config = validate_config(config)
    alert, expected_id = prepare_alert(config, body)
    if message_id != expected_id:
        raise InvalidTransportMessage("message ID does not match alert")
    digest = sha256(body).hexdigest()
    _initialize(db, config)
    alert_id = alert["data"]["essentials"]["alertId"]
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT payload_sha256 FROM bus_alerts WHERE alert_id=?",
                           (alert_id,)).fetchone()
        if row and row[0] != digest:
            raise InvalidTransportMessage("alert ID has a conflicting payload")
        if row is None:
            conn.execute("INSERT INTO bus_alerts(alert_id,payload_sha256,message_id) "
                         "VALUES (?,?,?)", (alert_id, digest, message_id))
    case = open_case(db, config["recipe"], alert, config["scope"])
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("UPDATE bus_alerts SET case_id=? WHERE alert_id=?",
                     (case["case_id"], alert_id))
    return {"message_id": message_id, "case_id": case["case_id"], "state": case["state"]}


def _message_body(message: object) -> bytes:
    body = getattr(message, "body", None)
    if isinstance(body, bytes):
        return body
    try:
        chunks = list(body)
    except (TypeError, ValueError):
        raise InvalidTransportMessage("message body is not binary data") from None
    if not chunks or any(not isinstance(part, bytes) for part in chunks):
        raise InvalidTransportMessage("message body is not binary data")
    if sum(map(len, chunks)) > MAX_BODY:
        raise InvalidTransportMessage("message body exceeds limit")
    return b"".join(chunks)


def settle_one(receiver: object, message: object, db: Path, config: dict) -> dict:
    """Complete valid cases; dead-letter malformed alerts; abandon worker failures."""
    try:
        body = _message_body(message)
        result = open_delivered_case(db, config, body, getattr(message, "message_id", None))
    except InvalidTransportMessage:
        receiver.dead_letter_message(message, reason="InvalidScopedAlert")
        return {"state": "dead_lettered", "reason": "InvalidScopedAlert"}
    except Exception:
        receiver.abandon_message(message)
        raise
    receiver.complete_message(message)
    return result


def _azure_client(namespace: str):
    try:
        from azure.identity import DefaultAzureCredential
        from azure.servicebus import ServiceBusClient
    except ImportError as exc:
        raise RuntimeError("install the azure extra: pip install -e '.[azure]'") from exc
    credential = DefaultAzureCredential(exclude_interactive_browser_credential=True)
    return ServiceBusClient(namespace, credential), credential


def publish_file(namespace: str, queue: str, config: dict, body: bytes) -> dict:
    """Authorized operator replay; this is not an Azure Monitor webhook endpoint."""
    namespace, queue = validate_address(namespace, queue)
    _, message_id = prepare_alert(config, body)
    try:
        from azure.servicebus import ServiceBusMessage
    except ImportError as exc:
        raise RuntimeError("install the azure extra: pip install -e '.[azure]'") from exc
    client, credential = _azure_client(namespace)
    try:
        with client, client.get_queue_sender(queue) as sender:
            sender.send_messages(ServiceBusMessage(body, message_id=message_id))
    finally:
        credential.close()
    return {"message_id": message_id, "state": "submitted"}


def consume_once(namespace: str, queue: str, db: Path, config: dict, *, wait: int = 5) -> dict | None:
    namespace, queue = validate_address(namespace, queue)
    if not 1 <= wait <= 30:
        raise ValueError("wait must be 1..30 seconds")
    config = validate_config(config)
    _initialize(db, config)
    client, credential = _azure_client(namespace)
    try:
        with client, client.get_queue_receiver(queue, max_wait_time=wait) as receiver:
            messages = receiver.receive_messages(max_message_count=1, max_wait_time=wait)
            if not messages:
                return None
            return settle_one(receiver, messages[0], db, config)
    finally:
        credential.close()


def consume_forever(namespace: str, queue: str, db: Path, config: dict, *, wait: int = 10,
                    on_result: Callable[[dict], None] | None = None) -> None:
    """Keep one AMQP receiver open until interrupted or a worker error occurs."""
    namespace, queue = validate_address(namespace, queue)
    if not 1 <= wait <= 30:
        raise ValueError("wait must be 1..30 seconds")
    config = validate_config(config)
    _initialize(db, config)
    client, credential = _azure_client(namespace)
    try:
        with client, client.get_queue_receiver(queue, max_wait_time=wait) as receiver:
            while True:
                messages = receiver.receive_messages(max_message_count=1, max_wait_time=wait)
                for message in messages:
                    result = settle_one(receiver, message, db, config)
                    if on_result is not None:
                        on_result(result)
    finally:
        credential.close()
