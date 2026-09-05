from __future__ import annotations

import hashlib
import hmac
import json
import os
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from .models import Observation, WorkItem


@dataclass(slots=True)
class PublishReceipt:
    destination: str
    external_id: str | None = None
    response: dict[str, Any] | None = None


class Publisher:
    def publish(self, destination: str, work_item: WorkItem, observations: list[Observation]) -> PublishReceipt:
        raise NotImplementedError


class WebhookPublisher(Publisher):
    """Publish WorkItems only to operator-configured destination URLs."""

    def __init__(self, destinations: dict[str, str], secret: str | None = None, timeout_s: float = 10.0):
        self.destinations = {key.casefold(): value for key, value in destinations.items()}
        self.secret = secret.encode("utf-8") if secret else None
        self.timeout_s = timeout_s

    def publish(self, destination: str, work_item: WorkItem, observations: list[Observation]) -> PublishReceipt:
        key = destination.casefold()
        url = self.destinations.get(key)
        if not url:
            raise KeyError(f"publisher destination not configured: {destination}")

        payload = {
            "schema": "aftergraph.work-item/1.0",
            "destination": destination,
            "work_item": _jsonable(asdict(work_item)),
            "observations": [_jsonable(asdict(item)) for item in observations],
        }
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        headers = {"Content-Type": "application/json", "User-Agent": "aftergraph-work-intelligence/0.1"}
        if self.secret:
            digest = hmac.new(self.secret, body, hashlib.sha256).hexdigest()
            headers["X-Aftergraph-Signature"] = f"sha256={digest}"

        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                response_body = response.read(1024 * 1024)
                content_type = response.headers.get("Content-Type", "")
                parsed: dict[str, Any]
                if "json" in content_type:
                    raw = json.loads(response_body.decode("utf-8") or "{}")
                    parsed = raw if isinstance(raw, dict) else {"value": raw}
                else:
                    parsed = {"status": response.status, "body": response_body.decode("utf-8", errors="replace")}
        except urllib.error.HTTPError as exc:
            body_text = exc.read(4096).decode("utf-8", errors="replace")
            raise RuntimeError(f"publisher {destination} returned HTTP {exc.code}: {body_text}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"publisher {destination} failed: {exc.reason}") from exc

        external_id = parsed.get("id") or parsed.get("external_id") or parsed.get("ticket_id")
        return PublishReceipt(destination=destination, external_id=str(external_id) if external_id is not None else None, response=parsed)


def publisher_from_env() -> Publisher | None:
    raw = os.getenv("AFTERGRAPH_PUBLISHERS_JSON", "").strip()
    if not raw:
        return None
    parsed = json.loads(raw)
    if not isinstance(parsed, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in parsed.items()):
        raise ValueError("AFTERGRAPH_PUBLISHERS_JSON must be a JSON object of destination -> URL")
    return WebhookPublisher(parsed, secret=os.getenv("AFTERGRAPH_WEBHOOK_SECRET"))


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value
