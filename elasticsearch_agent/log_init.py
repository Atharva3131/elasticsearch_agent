"""Structured, secret-redacting audit logging."""
from __future__ import annotations

import json
import logging
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from elasticsearch_agent.config import cfg

_SECRET = re.compile(r"(?i)(api[_-]?key|password|authorization|token|secret)\s*[:=]\s*([^,\s}]+)")
_URL_CREDENTIALS = re.compile(r"(?i)(https?://)([^/@\s]+):([^/@\s]+)@")


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: "<REDACTED>" if re.search(r"(?i)(key|password|authorization|token|secret)", k) else redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str):
        value = _URL_CREDENTIALS.sub(r"\1<REDACTED>:<REDACTED>@", value)
        return _SECRET.sub(lambda m: f"{m.group(1)}=<REDACTED>", value)
    return value


class AuditLogger:
    def __init__(self, path: Path, on_event=None, username: str = "anonymous", role: str = ""):
        self.path = path
        self.sequence = 0
        self.request_id = ""
        self.username = username
        self.role = role
        # Optional callback(record: dict) for live UI streaming (e.g. Streamlit).
        self.on_event = on_event

    def event(self, event: str, **fields: Any) -> dict[str, Any]:
        self.sequence += 1
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "request_id": self.request_id,
            "sequence": self.sequence,
            "event": event,
            "username": self.username,
            "role": self.role,
            **redact(fields),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")
        print(json.dumps(record, default=str), file=sys.stderr)
        if self.on_event:
            try:
                self.on_event(record)
            except Exception:
                pass
        return record

    def start(self, user_input: str) -> str:
        self.request_id, self.sequence = uuid.uuid4().hex, 0
        self.event("request.received", user_input=user_input)
        return self.request_id

    def timed_tool(self, name: str, args: dict[str, Any], fn, reason: str | None = None):
        started = time.perf_counter()
        extra = {"reason": reason} if reason else {}
        self.event("tool.started", tool=name, arguments=args, **extra)
        try:
            result = fn()
            completed_extra: dict[str, Any] = {}
            # Surface the chart artifact path directly so the UI can pick it up.
            if isinstance(result, dict) and "artifact_path" in result:
                completed_extra["artifact_path"] = result["artifact_path"]
            self.event("tool.completed", tool=name, status="success", duration_ms=round((time.perf_counter() - started) * 1000, 2), result_metadata=_metadata(result), **completed_extra)
            return result
        except Exception as exc:
            self.event("tool.failed", tool=name, status="failure", duration_ms=round((time.perf_counter() - started) * 1000, 2), error=repr(exc), error_type=type(exc).__name__)
            raise


def _metadata(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return {"keys": list(value)[:20], "count": len(value.get("hits", {}).get("hits", [])) if isinstance(value.get("hits"), dict) else None}
    return {"type": type(value).__name__}


logger = logging.getLogger("elasticsearch_agent")
logging.basicConfig(level=logging.INFO)
