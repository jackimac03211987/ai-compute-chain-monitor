# -*- coding: utf-8 -*-
"""Transactional local identities and one-time personal access tokens."""

import datetime as dt
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from argon2.low_level import Type, hash_secret_raw


SCHEMA_VERSION = 1
ARGON_TIME_COST = 3
ARGON_MEMORY_KIB = 65536
ARGON_PARALLELISM = 2
VALID_ROLES = {"owner", "administrator", "operator", "viewer"}
ROLE_PERMISSIONS = {
    "owner": frozenset({"*"}),
    "administrator": frozenset({
        "watchlist.read", "watchlist.write", "interfaces.read", "interfaces.write",
        "interfaces.test", "transfer.import", "transfer.export", "audit.read_own",
        "support.grant", "users.read_summary", "users.manage", "licenses.manage",
    }),
    "operator": frozenset({
        "watchlist.read", "watchlist.write", "interfaces.read", "interfaces.write",
        "interfaces.test", "transfer.import", "transfer.export", "audit.read_own",
        "support.grant",
    }),
    "viewer": frozenset({"watchlist.read", "interfaces.read", "audit.read_own", "support.grant"}),
}
SUPPORT_SCOPE_PERMISSIONS = {
    "watchlist": {"watchlist.read", "watchlist.write"},
    "interfaces": {"interfaces.read", "interfaces.write", "interfaces.test"},
    "transfer": {"transfer.import", "transfer.export"},
    "audit": {"audit.read_own"},
}


def _now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _parse_time(value):
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _hash_token(raw, salt):
    return hash_secret_raw(
        secret=raw.encode("utf-8"),
        salt=salt,
        time_cost=ARGON_TIME_COST,
        memory_cost=ARGON_MEMORY_KIB,
        parallelism=ARGON_PARALLELISM,
        hash_len=32,
        type=Type.ID,
    )


@dataclass(frozen=True)
class AuthContext:
    tenant_id: str
    user_id: str
    role: str
    permissions: frozenset
    token_id: str
    support_grant: object = None

    def allows(self, permission):
        return "*" in self.permissions or permission in self.permissions


class IdentityStore:
    def __init__(self, base):
        self.base = Path(base)
        self.path = self.base / "data" / "auth" / "aicm_identity.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        self._migrate()

    def _connect(self):
        connection = sqlite3.connect(str(self.path), timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    def _migrate(self):
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY, checksum TEXT NOT NULL, applied_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS tenants (
                    tenant_id TEXT PRIMARY KEY, name TEXT NOT NULL, status TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS users (
                    user_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
                    display_name TEXT NOT NULL, role TEXT NOT NULL, status TEXT NOT NULL,
                    device_quota INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS tokens (
                    token_id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(user_id),
                    token_prefix TEXT NOT NULL, lookup_hash TEXT NOT NULL UNIQUE,
                    salt BLOB NOT NULL, token_hash BLOB NOT NULL,
                    issued_at TEXT NOT NULL, last_used_at TEXT, expires_at TEXT, revoked_at TEXT
                );
                CREATE TABLE IF NOT EXISTS user_permissions (
                    user_id TEXT NOT NULL REFERENCES users(user_id), permission_key TEXT NOT NULL,
                    allowed INTEGER NOT NULL, PRIMARY KEY(user_id, permission_key)
                );
                CREATE TABLE IF NOT EXISTS support_grants (
                    grant_id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(user_id),
                    administrator_id TEXT NOT NULL REFERENCES users(user_id), scopes_json TEXT NOT NULL,
                    reason TEXT NOT NULL, issued_at TEXT NOT NULL, expires_at TEXT NOT NULL, revoked_at TEXT
                );
                CREATE TABLE IF NOT EXISTS identity_audit (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT, actor_user_id TEXT,
                    effective_user_id TEXT, action TEXT NOT NULL, result TEXT NOT NULL,
                    correlation_id TEXT, details_json TEXT NOT NULL, created_at TEXT NOT NULL
                );
                """
            )
            db.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, checksum, applied_at) VALUES(?,?,?)",
                (SCHEMA_VERSION, "identity-v1", _now()),
            )
        os.chmod(self.path, 0o600)

    def schema_version(self):
        with self._connect() as db:
            row = db.execute("SELECT MAX(version) AS version FROM schema_migrations").fetchone()
            return int(row["version"] or 0)

    def _permissions(self, db, user_id, role):
        values = set(ROLE_PERMISSIONS[role])
        for row in db.execute(
            "SELECT permission_key, allowed FROM user_permissions WHERE user_id=?", (user_id,)
        ):
            if row["allowed"]:
                values.add(row["permission_key"])
            else:
                values.discard(row["permission_key"])
        return frozenset(values)

    def _audit(self, db, action, result, actor=None, effective=None, details=None):
        db.execute(
            "INSERT INTO identity_audit(actor_user_id,effective_user_id,action,result,correlation_id,details_json,created_at) VALUES(?,?,?,?,?,?,?)",
            (actor, effective, action, result, secrets.token_hex(8), json.dumps(details or {}, separators=(",", ":")), _now()),
        )

    def bootstrap_owner(self, admin_token):
        raw = str(admin_token or "").strip()
        if not raw:
            raise ValueError("admin token is required")
        now = _now()
        with self._connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO tenants(tenant_id,name,status,created_at,updated_at) VALUES(?,?,?,?,?)",
                ("owner", "System Owner", "active", now, now),
            )
            db.execute(
                "INSERT OR IGNORE INTO users(user_id,tenant_id,display_name,role,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                ("owner", "owner", "System Owner", "owner", "active", now, now),
            )
            lookup = hashlib.sha256(raw.encode()).hexdigest()
            existing = db.execute("SELECT token_id FROM tokens WHERE lookup_hash=?", (lookup,)).fetchone()
            if not existing:
                salt = secrets.token_bytes(16)
                db.execute(
                    "INSERT OR REPLACE INTO tokens(token_id,user_id,token_prefix,lookup_hash,salt,token_hash,issued_at) VALUES(?,?,?,?,?,?,?)",
                    ("owner-admin", "owner", "owner-admin", lookup, salt, _hash_token(raw, salt), now),
                )
                self._audit(db, "owner.bootstrap", "success", "owner", "owner")
        return {"tenant_id": "owner", "user_id": "owner", "role": "owner"}

    def create_user(self, display_name, role, permissions=None):
        name = str(display_name or "").strip()
        role = str(role or "").strip().lower()
        if not name:
            raise ValueError("display name is required")
        if role not in VALID_ROLES or role == "owner":
            raise ValueError("role must be administrator, operator, or viewer")
        now = _now()
        tenant_id = "tenant_" + secrets.token_hex(8)
        user_id = "user_" + secrets.token_hex(8)
        with self._connect() as db:
            db.execute(
                "INSERT INTO tenants(tenant_id,name,status,created_at,updated_at) VALUES(?,?,?,?,?)",
                (tenant_id, name, "active", now, now),
            )
            db.execute(
                "INSERT INTO users(user_id,tenant_id,display_name,role,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                (user_id, tenant_id, name, role, "active", now, now),
            )
            for key, allowed in (permissions or {}).items():
                db.execute(
                    "INSERT INTO user_permissions(user_id,permission_key,allowed) VALUES(?,?,?)",
                    (user_id, str(key), 1 if allowed else 0),
                )
            self._audit(db, "user.create", "success", "owner", user_id, {"role": role})
        return {"tenant_id": tenant_id, "user_id": user_id, "display_name": name, "role": role, "status": "active"}

    def issue_token(self, user_id, expires_at=None):
        with self._connect() as db:
            user = db.execute("SELECT status FROM users WHERE user_id=?", (user_id,)).fetchone()
            if not user:
                raise ValueError("user not found")
            token_id = secrets.token_hex(12)
            raw = f"aicm_u_{token_id}_{secrets.token_urlsafe(32)}"
            salt = secrets.token_bytes(16)
            db.execute(
                "INSERT INTO tokens(token_id,user_id,token_prefix,lookup_hash,salt,token_hash,issued_at,expires_at) VALUES(?,?,?,?,?,?,?,?)",
                (token_id, user_id, raw[:24], hashlib.sha256(raw.encode()).hexdigest(), salt, _hash_token(raw, salt), _now(), expires_at),
            )
            self._audit(db, "token.issue", "success", "owner", user_id, {"token_id": token_id})
        return {"token_id": token_id, "token": raw, "expires_at": expires_at}

    def verify_token(self, raw_token):
        raw = str(raw_token or "").strip()
        if not raw:
            return None
        lookup = hashlib.sha256(raw.encode()).hexdigest()
        with self._connect() as db:
            row = db.execute(
                """SELECT t.*,u.tenant_id,u.role,u.status FROM tokens t
                   JOIN users u ON u.user_id=t.user_id WHERE t.lookup_hash=?""",
                (lookup,),
            ).fetchone()
            if not row or row["revoked_at"] or row["status"] != "active":
                return None
            expiry = _parse_time(row["expires_at"])
            if expiry and expiry <= dt.datetime.now(dt.timezone.utc):
                return None
            if not hmac.compare_digest(row["token_hash"], _hash_token(raw, row["salt"])):
                return None
            permissions = self._permissions(db, row["user_id"], row["role"])
            db.execute("UPDATE tokens SET last_used_at=? WHERE token_id=?", (_now(), row["token_id"]))
            return AuthContext(row["tenant_id"], row["user_id"], row["role"], permissions, row["token_id"])

    def revoke_token(self, token_id):
        with self._connect() as db:
            cursor = db.execute(
                "UPDATE tokens SET revoked_at=? WHERE token_id=? AND revoked_at IS NULL",
                (_now(), str(token_id)),
            )
            if cursor.rowcount:
                self._audit(db, "token.revoke", "success", "owner", None, {"token_id": str(token_id)})
            return bool(cursor.rowcount)

    def set_user_status(self, user_id, status):
        status = str(status or "").lower()
        if status not in {"active", "disabled"}:
            raise ValueError("status must be active or disabled")
        with self._connect() as db:
            cursor = db.execute(
                "UPDATE users SET status=?,updated_at=? WHERE user_id=?",
                (status, _now(), str(user_id)),
            )
            if not cursor.rowcount:
                raise ValueError("user not found")
            self._audit(db, "user.status", "success", "owner", str(user_id), {"status": status})
            row = db.execute("SELECT * FROM users WHERE user_id=?", (str(user_id),)).fetchone()
            return dict(row)

    def create_support_grant(self, user_auth, administrator_id, scopes, reason, expires_at):
        if not isinstance(user_auth, AuthContext) or not user_auth.allows("support.grant"):
            raise PermissionError("support grant permission required")
        clean_scopes = sorted({str(scope).strip().lower() for scope in (scopes or [])})
        if not clean_scopes or any(scope not in SUPPORT_SCOPE_PERMISSIONS for scope in clean_scopes):
            raise ValueError("support scopes must use watchlist, interfaces, transfer, or audit")
        clean_reason = str(reason or "").strip()
        if not clean_reason:
            raise ValueError("support reason is required")
        expiry = _parse_time(expires_at)
        now_dt = dt.datetime.now(dt.timezone.utc)
        if not expiry or expiry <= now_dt or expiry > now_dt + dt.timedelta(hours=24):
            raise ValueError("support grant expiry must be within 24 hours")
        grant_id = "grant_" + secrets.token_hex(12)
        with self._connect() as db:
            admin = db.execute(
                "SELECT role,status FROM users WHERE user_id=?", (str(administrator_id),)
            ).fetchone()
            if not admin or admin["status"] != "active" or admin["role"] not in {"owner", "administrator"}:
                raise ValueError("administrator is not active")
            db.execute(
                "INSERT INTO support_grants(grant_id,user_id,administrator_id,scopes_json,reason,issued_at,expires_at) VALUES(?,?,?,?,?,?,?)",
                (grant_id, user_auth.user_id, str(administrator_id), json.dumps(clean_scopes), clean_reason, _now(), expiry.isoformat()),
            )
            self._audit(
                db, "support.grant", "success", user_auth.user_id, user_auth.user_id,
                {"grant_id": grant_id, "administrator_id": str(administrator_id), "scopes": clean_scopes},
            )
        return {
            "grant_id": grant_id, "user_id": user_auth.user_id,
            "administrator_id": str(administrator_id), "scopes": clean_scopes,
            "reason": clean_reason, "expires_at": expiry.isoformat(),
        }

    def resolve_support_context(self, administrator_auth, grant_id):
        if not isinstance(administrator_auth, AuthContext) or administrator_auth.role not in {"owner", "administrator"}:
            raise PermissionError("administrator context required")
        with self._connect() as db:
            row = db.execute(
                """SELECT g.*,u.tenant_id,u.role,u.status FROM support_grants g
                   JOIN users u ON u.user_id=g.user_id WHERE g.grant_id=?""",
                (str(grant_id),),
            ).fetchone()
            if not row or row["revoked_at"] or row["status"] != "active":
                raise PermissionError("support grant is unavailable")
            if row["administrator_id"] != administrator_auth.user_id:
                raise PermissionError("support grant belongs to another administrator")
            expiry = _parse_time(row["expires_at"])
            if not expiry or expiry <= dt.datetime.now(dt.timezone.utc):
                raise PermissionError("support grant has expired")
            scopes = json.loads(row["scopes_json"])
            permissions = frozenset(
                permission for scope in scopes for permission in SUPPORT_SCOPE_PERMISSIONS[scope]
            )
            grant = {
                "grant_id": row["grant_id"], "administrator_id": row["administrator_id"],
                "scopes": scopes, "reason": row["reason"], "expires_at": row["expires_at"],
            }
            self._audit(
                db, "support.enter", "success", administrator_auth.user_id, row["user_id"],
                {"grant_id": row["grant_id"], "scopes": scopes},
            )
            return AuthContext(
                row["tenant_id"], row["user_id"], row["role"], permissions,
                administrator_auth.token_id, grant,
            )

    def revoke_support_grant(self, user_auth, grant_id):
        if not isinstance(user_auth, AuthContext):
            raise PermissionError("user context required")
        with self._connect() as db:
            cursor = db.execute(
                "UPDATE support_grants SET revoked_at=? WHERE grant_id=? AND user_id=? AND revoked_at IS NULL",
                (_now(), str(grant_id), user_auth.user_id),
            )
            if cursor.rowcount:
                self._audit(
                    db, "support.revoke", "success", user_auth.user_id, user_auth.user_id,
                    {"grant_id": str(grant_id)},
                )
            return bool(cursor.rowcount)

    def list_audit(self, limit=1000):
        size = max(1, min(int(limit), 1000))
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM identity_audit ORDER BY event_id DESC LIMIT ?", (size,)
            ).fetchall()
            output = []
            for row in rows:
                item = dict(row)
                item["details"] = json.loads(item.pop("details_json") or "{}")
                output.append(item)
            return output

    def list_user_audit(self, user_auth, limit=200):
        if not isinstance(user_auth, AuthContext) or not user_auth.allows("audit.read_own"):
            raise PermissionError("audit permission required")
        size = max(1, min(int(limit), 1000))
        with self._connect() as db:
            rows = db.execute(
                """SELECT * FROM identity_audit
                   WHERE effective_user_id=? OR actor_user_id=?
                   ORDER BY event_id DESC LIMIT ?""",
                (user_auth.user_id, user_auth.user_id, size),
            ).fetchall()
            output = []
            for row in rows:
                item = dict(row)
                item["details"] = json.loads(item.pop("details_json") or "{}")
                output.append(item)
            return {"items": output, "total": len(output)}

    def list_users(self):
        with self._connect() as db:
            rows = db.execute(
                """SELECT u.user_id,u.tenant_id,u.display_name,u.role,u.status,u.device_quota,
                          u.created_at,u.updated_at,COUNT(t.token_id) AS token_count,
                          MAX(t.last_used_at) AS last_activity
                   FROM users u LEFT JOIN tokens t ON t.user_id=u.user_id AND t.revoked_at IS NULL
                   GROUP BY u.user_id ORDER BY u.created_at"""
            ).fetchall()
            return {"items": [dict(row) for row in rows], "total": len(rows)}

    def monitor_contexts(self):
        contexts=[]
        with self._connect() as db:
            rows=db.execute(
                "SELECT user_id,tenant_id,role FROM users WHERE status='active' AND role!='owner' ORDER BY created_at"
            ).fetchall()
            for row in rows:
                permissions=self._permissions(db,row["user_id"],row["role"])
                if "interfaces.read" in permissions and "interfaces.test" in permissions:
                    contexts.append(AuthContext(row["tenant_id"],row["user_id"],row["role"],permissions,"scheduler"))
        return contexts

    def revoke_user_tokens(self, user_id):
        with self._connect() as db:
            cursor = db.execute(
                "UPDATE tokens SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL",
                (_now(), str(user_id)),
            )
            self._audit(db, "token.revoke_all", "success", "owner", str(user_id), {"count": cursor.rowcount})
            return int(cursor.rowcount)
