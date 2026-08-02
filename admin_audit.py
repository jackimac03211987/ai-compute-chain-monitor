# -*- coding: utf-8 -*-
"""Bounded, append-only audit trail for admin actions."""

import contextlib
import datetime
import fcntl
import json
import os
import secrets
from collections import Counter
from pathlib import Path

from admin_common import now_iso


MAX_EVENTS = 10000
PAGE_SIZES = {20, 50, 100}
SENSITIVE_TERMS = ("token", "authorization", "password", "secret", "api_key", "apikey")


def redact(value):
    if isinstance(value, dict):
        output = {}
        for key, item in value.items():
            normalized = str(key).lower().replace("-", "_")
            output[key] = (
                "[REDACTED]"
                if any(term in normalized for term in SENSITIVE_TERMS)
                else redact(item)
            )
        return output
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, tuple):
        return [redact(item) for item in value]
    return value


def _data_path(base, name):
    path = Path(base) / "data" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


@contextlib.contextmanager
def _audit_lock(base):
    lock_path = _data_path(base, "admin_audit.lock")
    with lock_path.open("w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _read_lines(path):
    if not path.exists():
        return []
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _replace_lines(path, lines):
    tmp = path.with_suffix(path.suffix + ".tmp")
    body = "\n".join(lines)
    if body:
        body += "\n"
    tmp.write_text(body, encoding="utf-8")
    os.replace(tmp, path)


def _rotate(base, path, lines):
    if len(lines) <= MAX_EVENTS:
        return lines
    overflow = lines[:-MAX_EVENTS]
    kept = lines[-MAX_EVENTS:]
    date_key = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d")
    archive = _data_path(base, f"admin_audit.{date_key}.jsonl")
    with archive.open("a", encoding="utf-8") as handle:
        for line in overflow:
            handle.write(line + "\n")
    _replace_lines(path, kept)
    return kept


def append_event(base, action, result, **fields):
    event = redact({
        "event_id": secrets.token_hex(12),
        "timestamp": now_iso(),
        "action": str(action),
        "result": str(result),
        **fields,
    })
    path = _data_path(base, "admin_audit.jsonl")
    line = json.dumps(event, ensure_ascii=False, sort_keys=True)
    with _audit_lock(base):
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        _rotate(base, path, _read_lines(path))
    return event


def _parse_event(line):
    try:
        payload = json.loads(line)
        return payload if isinstance(payload, dict) else None
    except (TypeError, ValueError):
        return None


def _parse_int(params, name, default):
    try:
        return int(params.get(name, default))
    except (TypeError, ValueError):
        return default


def query_events(base, params=None):
    params = params or {}
    path = _data_path(base, "admin_audit.jsonl")
    with _audit_lock(base):
        events = [event for event in (_parse_event(line) for line in _read_lines(path)) if event]
    events.reverse()

    action = str(params.get("action") or "").strip().lower()
    result = str(params.get("result") or "").strip().lower()
    date_from = str(params.get("date_from") or "").strip()
    date_to = str(params.get("date_to") or "").strip()
    if action:
        events = [item for item in events if str(item.get("action") or "").lower() == action]
    if result:
        events = [item for item in events if str(item.get("result") or "").lower() == result]
    if date_from:
        events = [item for item in events if str(item.get("timestamp") or "")[:10] >= date_from]
    if date_to:
        events = [item for item in events if str(item.get("timestamp") or "")[:10] <= date_to]

    page = max(1, _parse_int(params, "page", 1))
    page_size = _parse_int(params, "page_size", 50)
    if page_size not in PAGE_SIZES:
        page_size = 50
    total = len(events)
    start = (page - 1) * page_size
    return {
        "items": events[start:start + page_size],
        "page": page,
        "page_size": page_size,
        "total": total,
        "facets": {
            "actions": [
                {"value": key, "count": count}
                for key, count in sorted(Counter(str(item.get("action") or "") for item in events).items())
            ],
            "results": [
                {"value": key, "count": count}
                for key, count in sorted(Counter(str(item.get("result") or "") for item in events).items())
            ],
        },
    }
