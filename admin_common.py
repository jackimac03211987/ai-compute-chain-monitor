# -*- coding: utf-8 -*-
"""Shared contracts for the token-protected admin console."""

import datetime
import secrets
from pathlib import Path
from urllib.parse import unquote, urlparse


class ApiError(Exception):
    def __init__(self, status, code, message, details=None):
        super().__init__(message)
        self.status = int(status)
        self.code = str(code)
        self.message = str(message)
        self.details = details or []


def now_iso():
    return (
        datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
    )


def api_envelope(data=None, error=None, request_id=None):
    return {
        "ok": error is None,
        "data": data,
        "error": error,
        "meta": {
            "request_id": request_id or secrets.token_hex(8),
            "generated_at": now_iso(),
        },
    }


def safe_static_path(base, request_path):
    base = Path(base).resolve()
    path = unquote(urlparse(request_path).path)
    rel = path.lstrip("/")
    if path.endswith("/"):
        rel += "index.html"
    rel = rel or "index.html"
    candidate = (base / rel).resolve()
    if candidate != base and base not in candidate.parents:
        raise ApiError(404, "not_found", "Resource not found")
    return candidate


def read_limited_body(handler, limit):
    try:
        length = int(handler.headers.get("Content-Length") or 0)
    except (TypeError, ValueError) as exc:
        raise ApiError(400, "invalid_content_length", "Invalid Content-Length") from exc
    if length < 0:
        raise ApiError(400, "invalid_content_length", "Invalid Content-Length")
    if length > int(limit):
        raise ApiError(413, "payload_too_large", "Request body is too large")
    return handler.rfile.read(length) if length else b""
