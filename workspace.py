# -*- coding: utf-8 -*-
"""Safe filesystem boundaries for private user workspaces."""

import os
import re
from pathlib import Path

from aicm_io import data_lock, read_json, write_json_atomic
from identity_store import AuthContext


ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
PRIVATE_NAMES = {
    "watchlist.json": "watchlist.json",
    "interface_registry.json": "interface_registry.json",
    "interface_results.json": "interface_results.json",
    "audit.jsonl": "audit.jsonl",
    "previews": "previews",
    "backups": "backups",
}
OWNER_NAMES = {
    **PRIVATE_NAMES,
    "watchlist.json": "user_watchlist.json",
}


class WorkspaceContext:
    def __init__(self, base, auth, owner_compat=False):
        if not isinstance(auth, AuthContext):
            raise ValueError("authenticated context is required")
        if not ID_PATTERN.fullmatch(auth.tenant_id) or not ID_PATTERN.fullmatch(auth.user_id):
            raise ValueError("invalid workspace identity")
        self.base = Path(base).resolve()
        self.auth = auth
        self.owner_compat = bool(owner_compat and auth.role == "owner")
        if self.owner_compat:
            self.root = self.base / "data"
        else:
            self.root = self.base / "data" / "tenants" / auth.tenant_id / "users" / auth.user_id
        self.ensure()

    def ensure(self):
        self.root.mkdir(parents=True, exist_ok=True)
        if not self.owner_compat:
            current = self.root
            stop = self.base / "data"
            while current != stop and stop in current.parents:
                os.chmod(current, 0o700)
                current = current.parent
        return self.root

    def path(self, name):
        key = str(name or "")
        mapping = OWNER_NAMES if self.owner_compat else PRIVATE_NAMES
        if key not in mapping:
            raise ValueError("unsupported workspace path")
        target = (self.root / mapping[key]).resolve()
        root = self.root.resolve()
        if target != root and root not in target.parents:
            raise ValueError("workspace path escapes root")
        return target

    def read_json(self, name, default=None):
        return read_json(self.path(name), {} if default is None else default)

    def write_json(self, name, payload):
        target = self.path(name)
        target.parent.mkdir(parents=True, exist_ok=True)
        with data_lock(self.base):
            write_json_atomic(target, payload)
            os.chmod(target, 0o600)
        return target
