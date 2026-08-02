# -*- coding: utf-8 -*-
"""Bounded authentication failure tracking for the HTTP service."""

import hashlib
import os
import threading
import time
from collections import OrderedDict, deque


def _positive_int(name, default, minimum=1, maximum=86400):
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


class AuthRateLimiter:
    """In-memory limiter scoped to a client identity and auth surface."""

    def __init__(self, threshold=None, window_seconds=None, max_entries=None, clock=None):
        self.threshold = threshold or _positive_int("AICM_AUTH_FAILURE_LIMIT", 5, 2, 100)
        self.window_seconds = window_seconds or _positive_int("AICM_AUTH_FAILURE_WINDOW", 300, 10)
        self.max_entries = max_entries or _positive_int("AICM_AUTH_BUCKETS", 2048, 64, 100000)
        self.clock = clock or time.monotonic
        self.lock = threading.Lock()
        self.buckets = OrderedDict()
        self.backoff = (60, 180, 600)

    def _bucket(self, key, now):
        bucket = self.buckets.pop(key, None)
        if bucket is None:
            bucket = {"failures": deque(), "lock_until": 0.0, "lock_level": 0}
        self.buckets[key] = bucket
        cutoff = now - self.window_seconds
        while bucket["failures"] and bucket["failures"][0] < cutoff:
            bucket["failures"].popleft()
        while len(self.buckets) > self.max_entries:
            self.buckets.popitem(last=False)
        return bucket

    def retry_after(self, key):
        if not enabled("AICM_AUTH_RATELIMIT", True):
            return 0
        now = self.clock()
        with self.lock:
            bucket = self._bucket(key, now)
            return max(0, int(bucket["lock_until"] - now + 0.999))

    def failure(self, key):
        if not enabled("AICM_AUTH_RATELIMIT", True):
            return 0
        now = self.clock()
        with self.lock:
            bucket = self._bucket(key, now)
            bucket["failures"].append(now)
            if len(bucket["failures"]) >= self.threshold:
                level = min(bucket["lock_level"], len(self.backoff) - 1)
                bucket["lock_until"] = max(bucket["lock_until"], now + self.backoff[level])
                bucket["lock_level"] = min(level + 1, len(self.backoff) - 1)
            return max(0, int(bucket["lock_until"] - now + 0.999))

    def success(self, key):
        with self.lock:
            self.buckets.pop(key, None)

    def clear(self):
        with self.lock:
            self.buckets.clear()


def enabled(name, default=False):
    fallback = "1" if default else "0"
    return str(os.getenv(name, fallback)).strip().lower() in {"1", "true", "yes", "on"}


def client_identity(handler):
    peer = str(handler.client_address[0])
    trusted_proxy = peer in {"127.0.0.1", "::1"} and enabled("AICM_TRUST_TAILSCALE_HEADERS", False)
    login = handler.headers.get("Tailscale-User-Login", "").strip() if trusted_proxy else ""
    if login:
        digest = hashlib.sha256(login.lower().encode("utf-8")).hexdigest()[:20]
        return f"tailscale:{digest}"
    return f"ip:{peer}"


AUTH_LIMITER = AuthRateLimiter()
