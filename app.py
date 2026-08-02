# -*- coding: utf-8 -*-
"""Static site + watchlist API for the AI compute chain monitor."""

import json
import os
import secrets
import threading
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from admin_audit import append_event, query_events
from admin_common import ApiError, api_envelope, read_limited_body, safe_static_path
from admin_jobs import AdminJobManager
from admin_service import (
    build_overview,
    list_interfaces,
    query_companies,
    toggle_company,
    update_company,
)
from admin_transfer import (
    apply_preview,
    create_preview,
    export_bytes,
    load_preview,
    parse_upload,
    template_bytes,
)
from aicm_io import data_lock, read_json, write_json_atomic
from auth_context import require_permission
from identity_store import IdentityStore
from interface_probe import probe_interface
from interface_credentials import KeychainStore
from interface_registry import (
    create_record as create_private_interface,
    delete_record as delete_private_interface,
    get_record as get_private_interface,
    list_records as list_private_interfaces,
    update_record as update_private_interface,
)
from interface_scheduler import InterfaceMonitorScheduler, InterfaceResultStore
from interface_transfer import (
    apply_preview as apply_private_interface_preview,
    create_preview as create_private_interface_preview,
    export_interfaces as export_private_interfaces,
    interface_template_bytes,
    parse_upload as parse_private_interface_upload,
)
from personal_watchlist import add_personal_tickers, list_personal_watchlist, remove_personal_tickers
from security_controls import AUTH_LIMITER, client_identity, enabled
from workspace import WorkspaceContext
from ticker_meta import normalize_ticker, resolve_ticker
from watchlist_loader import (
    ACTIVE_UNIVERSE_NAME,
    default_user_watchlist,
    load_active_universe,
    load_user_watchlist,
    rebuild_active_universe,
    write_user_watchlist,
)


BASE = Path(__file__).parent
DATA = BASE / "data"
ADMIN_TOKEN_PATH = DATA / "admin_token.txt"
_ADMIN_JOB_MANAGERS = {}
_IDENTITY_STORES = {}
_PRIVATE_MONITORS = {}


def reset_admin_runtime():
    for monitor in list(_PRIVATE_MONITORS.values()):
        monitor.stop()
    _PRIVATE_MONITORS.clear()
    _ADMIN_JOB_MANAGERS.clear()
    _IDENTITY_STORES.clear()
    AUTH_LIMITER.clear()


def identity_store():
    key = str(BASE.resolve())
    if key not in _IDENTITY_STORES:
        store = IdentityStore(BASE)
        store.bootstrap_owner(ensure_admin_token())
        _IDENTITY_STORES[key] = store
    return _IDENTITY_STORES[key]


def admin_job_manager():
    key = str(BASE.resolve())
    if key not in _ADMIN_JOB_MANAGERS:
        _ADMIN_JOB_MANAGERS[key] = AdminJobManager(BASE)
    return _ADMIN_JOB_MANAGERS[key]


def private_monitor_items():
    items=[]
    for auth in identity_store().monitor_contexts():
        workspace=WorkspaceContext(BASE,auth)
        result_store=InterfaceResultStore(workspace)
        for record in list_private_interfaces(workspace)["items"]:
            if not record.get("enabled",True):
                continue
            interface_id=record["id"]
            host=urlparse(record.get("url") or "").hostname or "local"
            def operation(record=record,auth=auth):
                return probe_interface(BASE,record,KeychainStore(auth))
            def callback(result,result_store=result_store,interface_id=interface_id):
                previous=result_store.latest(interface_id) or {}
                payload=dict(result)
                if payload.get("status")=="healthy": payload["consecutive_failures"]=0
                else: payload["consecutive_failures"]=int(previous.get("consecutive_failures") or 0)+1
                result_store.record(interface_id,payload)
            items.append({
                "key":(auth.tenant_id,auth.user_id,interface_id),"host":host,
                "interval_minutes":record.get("interval_minutes",15),"latest":result_store.latest(interface_id),
                "operation":operation,"callback":callback,"enabled":True,"budgeted":bool(record.get("url")),
            })
    return items


def private_monitor_runtime():
    key=str(BASE.resolve())
    if key not in _PRIVATE_MONITORS:
        monitor=InterfaceMonitorScheduler(max_workers=4,per_host_limit=2,poll_seconds=10)
        monitor.start(private_monitor_items); _PRIVATE_MONITORS[key]=monitor
    return _PRIVATE_MONITORS[key]


def query_params(path):
    values = parse_qs(urlparse(path).query, keep_blank_values=True)
    return {key: items[-1] if items else "" for key, items in values.items()}


def truthy(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def credential_bundle(payload):
    auth_type=str(payload.get("auth_type") or "").strip().lower()
    if auth_type=="bearer":
        bundle={"type":"bearer","token":str(payload.get("token") or "").strip()}
        required=bundle["token"]
    elif auth_type=="api_key":
        bundle={"type":"api_key","header":str(payload.get("header") or "X-API-Key").strip(),"value":str(payload.get("value") or "").strip()}
        required=bundle["value"]
    elif auth_type=="basic":
        bundle={"type":"basic","username":str(payload.get("username") or "").strip(),"password":str(payload.get("password") or "")}
        required=bundle["username"] and bundle["password"]
    elif auth_type=="secret_headers":
        headers=payload.get("headers") or {}
        if not isinstance(headers,dict): raise ApiError(400,"invalid_credentials","Secret headers must be an object")
        bundle={"type":"secret_headers","headers":{str(k):str(v) for k,v in headers.items()}}
        required=bool(bundle["headers"])
    else:
        raise ApiError(400,"invalid_credentials","Unsupported credential type")
    if not required: raise ApiError(400,"invalid_credentials","Credential value is required")
    return auth_type,bundle


def default_companies():
    src = (BASE / "fetch_data.py").read_text(encoding="utf-8")
    marker = "\nwith data_lock(BASE):"
    if marker not in src:
        marker = "\nrebuild_active_universe(BASE, C)"
    ns = {"__file__": str(BASE / "fetch_data.py")}
    exec(src.split(marker, 1)[0], ns)
    return ns["C"]


def ensure_admin_token():
    env = os.getenv("AICM_ADMIN_TOKEN", "").strip()
    if env:
        return env
    if ADMIN_TOKEN_PATH.exists():
        return ADMIN_TOKEN_PATH.read_text(encoding="utf-8").strip()
    token = secrets.token_urlsafe(24)
    DATA.mkdir(exist_ok=True)
    ADMIN_TOKEN_PATH.write_text(token + "\n", encoding="utf-8")
    os.chmod(ADMIN_TOKEN_PATH, 0o600)
    return token


def parse_json_body(handler):
    raw = read_limited_body(handler, 1024 * 1024)
    if not raw: return {}
    return json.loads(raw.decode("utf-8"))


def parse_import_text(text):
    rows = []
    for line in str(text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "," in line:
            ticker, name = line.split(",", 1)
        elif "\t" in line:
            ticker, name = line.split("\t", 1)
        else:
            parts = line.split(None, 1)
            ticker = parts[0]
            name = parts[1] if len(parts) > 1 else ""
        rows.append({"ticker": ticker.strip(), "name": name.strip()})
    return rows


def active_payload():
    payload = load_active_universe(BASE)
    if payload:
        return payload
    return rebuild_active_universe(BASE, default_companies())


def health_payload():
    active = active_payload()
    user = load_user_watchlist(BASE)
    semi = read_json(DATA / "semi_market.json", {})
    live = read_json(DATA / "live.json", {})
    status = read_json(DATA / "live_status.json", {})
    monitor = _PRIVATE_MONITORS.get(str(BASE.resolve()))
    return {
        "ok": True,
        "service": "ai-compute-chain-monitor",
        "watchlist_count": len(read_json(DATA / "watchlist.json", {}).get("companies", [])),
        "user_watchlist_count": len(user.get("add", {})),
        "replace_base": bool(user.get("replace_base")),
        "active_universe_count": len(active.get("companies", [])),
        "semi_market_count": len(semi.get("companies", [])),
        "live_item_count": len(live.get("items", {})),
        "last_live_success": status.get("last_success") or status.get("finished_at"),
        "live_status": status.get("status"),
        "active_universe_file": f"data/{ACTIVE_UNIVERSE_NAME}",
        "monitor_scheduler": monitor.stats() if monitor else {"status": "not_started"},
    }


class AppHandler(SimpleHTTPRequestHandler):
    server_version = "AICM/1.1"
    protocol_version = "HTTP/1.1"

    def translate_path(self, path):
        try:
            return str(safe_static_path(BASE, path))
        except ApiError:
            return str(BASE / "__not_found__")

    def end_headers(self):
        self.send_header("Cache-Control", "no-store" if self.path.startswith("/api/") else "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header("Content-Security-Policy", "base-uri 'self'; object-src 'none'; frame-ancestors 'none'")
        super().end_headers()

    def send_json(self, payload, status=HTTPStatus.OK, extra_headers=None):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for key, value in (extra_headers or {}).items():
            self.send_header(str(key), str(value))
        self.end_headers()
        self.wfile.write(body)

    def send_admin_json(self, data=None, status=HTTPStatus.OK, error=None, extra_headers=None):
        self.send_json(api_envelope(data=data, error=error), status, extra_headers)

    def send_admin_error(self, status, code, message, details=None, extra_headers=None):
        self.send_admin_json(
            status=status,
            error={
                "code": str(code),
                "message": str(message),
                "details": details or [],
            },
            extra_headers=extra_headers,
        )

    def send_file(self, body, filename, content_type):
        body = bytes(body)
        encoded = quote(str(filename), safe="")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header(
            "Content-Disposition",
            f"attachment; filename*=UTF-8''{encoded}",
        )
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_error_json(self, status, message, errors=None):
        self.send_json({"ok": False, "message": message, "errors": errors or []}, status)

    def require_token(self, admin=False):
        if os.getenv("AICM_REQUIRE_TOKEN", "1") in ("0", "false", "False"):
            return True
        expected = ensure_admin_token()
        supplied = self.headers.get("X-AICM-Admin-Token") or self.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        key = ("admin" if admin else "legacy", client_identity(self))
        if supplied and secrets.compare_digest(supplied, expected):
            AUTH_LIMITER.success(key)
            return True
        retry_after = AUTH_LIMITER.retry_after(key)
        if retry_after:
            append_event(BASE, "auth.verify", "rate_limited", client=key[1], retry_after=retry_after)
            if admin:
                self.send_admin_error(429, "auth_rate_limited", "Too many authentication failures", extra_headers={"Retry-After": retry_after})
            else:
                self.send_json({"ok": False, "message": "too many authentication failures", "errors": []}, 429, {"Retry-After": retry_after})
            return False
        AUTH_LIMITER.failure(key)
        if admin:
            append_event(BASE, "auth.verify", "failed", client=key[1])
            self.send_admin_error(
                HTTPStatus.FORBIDDEN,
                "admin_token_required",
                "Valid admin token required",
            )
        else:
            self.send_error_json(HTTPStatus.FORBIDDEN, "admin token required")
        return False

    def parse_admin_json(self, limit=1024 * 1024):
        raw = read_limited_body(self, limit)
        if not raw:
            return {}
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise ApiError(400, "invalid_json", "Request body must be valid UTF-8 JSON") from exc
        if not isinstance(payload, dict):
            raise ApiError(400, "invalid_json", "JSON request body must be an object")
        return payload

    def require_personal_auth(self):
        supplied = self.headers.get("X-AICM-User-Token") or self.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        key = ("personal", client_identity(self))
        retry_after = AUTH_LIMITER.retry_after(key)
        if retry_after:
            append_event(BASE, "auth.personal.verify", "rate_limited", client=key[1], retry_after=retry_after)
            self.send_admin_error(429, "auth_rate_limited", "Too many authentication failures", extra_headers={"Retry-After": retry_after})
            return None
        auth = identity_store().verify_token(supplied)
        if auth and auth.role != "owner":
            AUTH_LIMITER.success(key)
            return auth
        AUTH_LIMITER.failure(key)
        append_event(BASE, "auth.personal.verify", "failed", client=key[1])
        self.send_admin_error(HTTPStatus.FORBIDDEN, "personal_token_required", "Valid personal token required")
        return None

    def handle_admin_error(self, exc):
        if isinstance(exc, ApiError):
            self.send_admin_error(exc.status, exc.code, exc.message, exc.details)
            return
        if isinstance(exc, PermissionError):
            self.send_admin_error(HTTPStatus.FORBIDDEN, "permission_denied", str(exc))
            return
        if isinstance(exc, (TypeError, ValueError)):
            self.send_admin_error(HTTPStatus.BAD_REQUEST, "invalid_request", str(exc))
            return
        self.log_error("admin API error: %s", exc)
        self.send_admin_error(
            HTTPStatus.INTERNAL_SERVER_ERROR,
            "internal_error",
            "Admin operation failed",
        )

    def do_GET(self):
        path = urlparse(self.path).path
        if path.startswith("/api/v1/me/"):
            auth = self.require_personal_auth()
            if not auth:
                return
            try:
                self.handle_private_get(path, auth)
            except Exception as exc:
                self.handle_admin_error(exc)
            return
        if path.startswith("/api/admin/"):
            if not self.require_token(admin=True):
                return
            try:
                self.handle_admin_get(path)
            except Exception as exc:
                self.handle_admin_error(exc)
            return
        if path == "/api/health":
            self.send_json(health_payload())
            return
        if path == "/api/watchlist":
            self.send_json({
                "ok": True,
                "user": load_user_watchlist(BASE),
                "active": active_payload(),
            })
            return
        return super().do_GET()

    def handle_admin_get(self, path):
        params = query_params(self.path)
        if path.startswith("/api/admin/support-grants/") and path.endswith("/profile"):
            grant_id = path.split("/")[-2]
            administrator = identity_store().verify_token(ensure_admin_token())
            support = identity_store().resolve_support_context(administrator, grant_id)
            self.send_admin_json({
                "effective_user_id": support.user_id,
                "tenant_id": support.tenant_id,
                "permissions": sorted(support.permissions),
                "support_grant": support.support_grant,
            })
            return
        if path == "/api/admin/users":
            self.send_admin_json(identity_store().list_users())
            return
        if path == "/api/admin/overview":
            self.send_admin_json(build_overview(BASE))
            return
        if path == "/api/admin/interfaces":
            self.send_admin_json({"items": list_interfaces(BASE)})
            return
        if path == "/api/admin/companies":
            self.send_admin_json(query_companies(BASE, params))
            return
        if path == "/api/admin/templates/companies.xlsx":
            body, filename, content_type = template_bytes(params.get("variant", "blank"))
            self.send_file(body, filename, content_type)
            return
        if path.startswith("/api/admin/import/previews/"):
            preview_id = path.rsplit("/", 1)[-1]
            self.send_admin_json(load_preview(BASE, preview_id))
            return
        if path == "/api/admin/export":
            export_format = params.get("format", "xlsx")
            scope = params.get("scope", "active")
            filters = None
            if scope == "filtered":
                rows = []
                page = 1
                while True:
                    page_params = {**params, "page": str(page), "page_size": "100"}
                    result = query_companies(BASE, page_params)
                    rows.extend(result["items"])
                    if len(rows) >= result["total"]:
                        break
                    page += 1
                filters = {"rows": rows}
            body, filename, content_type = export_bytes(
                BASE,
                export_format,
                scope,
                filters=filters,
                include_quotes=truthy(params.get("include_quotes")),
            )
            append_event(
                BASE,
                "export",
                "success",
                format=export_format,
                scope=scope,
                size_bytes=len(body),
            )
            self.send_file(body, filename, content_type)
            return
        if path == "/api/admin/jobs":
            self.send_admin_json(admin_job_manager().list(params))
            return
        if path.startswith("/api/admin/jobs/"):
            self.send_admin_json(admin_job_manager().get(path.rsplit("/", 1)[-1]))
            return
        if path == "/api/admin/audit":
            self.send_admin_json(query_events(BASE, params))
            return
        raise ApiError(404, "unknown_admin_path", "Unknown admin API path")

    def handle_private_get(self, path, auth):
        workspace = WorkspaceContext(BASE, auth)
        params = query_params(self.path)
        if path == "/api/v1/me/profile":
            self.send_admin_json({"tenant_id": auth.tenant_id, "user_id": auth.user_id, "role": auth.role, "permissions": sorted(auth.permissions)})
            return
        if path == "/api/v1/me/watchlist":
            self.send_admin_json(list_personal_watchlist(workspace, read_json(DATA / "live.json", {})))
            return
        if path == "/api/v1/me/interfaces":
            self.send_admin_json(list_private_interfaces(workspace, params))
            return
        if path == "/api/v1/me/interfaces/template":
            body, filename, content_type = interface_template_bytes(params.get("variant", "blank"))
            self.send_file(body, filename, content_type)
            return
        if path == "/api/v1/me/interfaces/export":
            body, filename, content_type = export_private_interfaces(workspace, params.get("format", "json"))
            self.send_file(body, filename, content_type)
            return
        if path.startswith("/api/v1/me/interfaces/") and path.endswith("/history"):
            interface_id = path.split("/")[-2]
            get_private_interface(workspace, interface_id)
            self.send_admin_json(InterfaceResultStore(workspace).history(interface_id, params.get("limit", 100)))
            return
        if path == "/api/v1/me/audit":
            self.send_admin_json(identity_store().list_user_audit(auth, params.get("limit", 200)))
            return
        raise ApiError(404, "unknown_private_path", "Unknown private API path")

    def do_POST(self):
        path = urlparse(self.path).path
        if not path.startswith("/api/"):
            self.send_error_json(HTTPStatus.NOT_FOUND, "not found")
            return
        if path.startswith("/api/v1/me/"):
            auth = self.require_personal_auth()
            if not auth:
                return
            try:
                self.handle_private_post(path, auth)
            except Exception as exc:
                self.handle_admin_error(exc)
            return
        if path.startswith("/api/admin/"):
            if not self.require_token(admin=True):
                return
            try:
                self.handle_admin_post(path)
            except Exception as exc:
                self.handle_admin_error(exc)
            return
        if not self.require_token(admin=False):
            return
        try:
            body = parse_json_body(self)
            if path == "/api/watchlist/add":
                self.handle_add(body)
            elif path == "/api/watchlist/import":
                rows = parse_import_text(body.get("text", ""))
                self.handle_add({"items": rows})
            elif path == "/api/watchlist/remove":
                self.handle_remove(body)
            elif path == "/api/watchlist/clear_base":
                self.handle_clear_base(body)
            elif path == "/api/watchlist/update_meta":
                self.handle_update_meta(body)
            else:
                self.send_error_json(HTTPStatus.NOT_FOUND, "unknown API path")
        except Exception as exc:
            self.send_error_json(HTTPStatus.BAD_REQUEST, str(exc))

    def do_PATCH(self):
        path = urlparse(self.path).path
        if not path.startswith("/api/v1/me/interfaces/"):
            self.send_admin_error(HTTPStatus.NOT_FOUND, "unknown_private_path", "Unknown private API path")
            return
        auth = self.require_personal_auth()
        if not auth:
            return
        try:
            interface_id = path.rsplit("/", 1)[-1]
            workspace = WorkspaceContext(BASE, auth)
            self.send_admin_json(update_private_interface(workspace, interface_id, self.parse_admin_json()))
        except Exception as exc:
            self.handle_admin_error(exc)

    def do_DELETE(self):
        path = urlparse(self.path).path
        if not path.startswith("/api/v1/me/interfaces/"):
            self.send_admin_error(HTTPStatus.NOT_FOUND, "unknown_private_path", "Unknown private API path")
            return
        auth = self.require_personal_auth()
        if not auth:
            return
        try:
            workspace = WorkspaceContext(BASE, auth)
            if path.endswith("/credentials"):
                interface_id=path.split("/")[-2]
                require_permission(auth,"interfaces.write"); get_private_interface(workspace,interface_id)
                KeychainStore(auth).delete(interface_id)
                record=update_private_interface(workspace,interface_id,{"auth_type":"none","credential_configured":False})
                self.send_admin_json({"id":record["id"],"auth_type":"none","credential_configured":False})
                return
            interface_id = path.rsplit("/", 1)[-1]
            self.send_admin_json(delete_private_interface(workspace, interface_id))
        except Exception as exc:
            self.handle_admin_error(exc)

    def handle_admin_post(self, path):
        if path == "/api/admin/import/preview":
            params = query_params(self.path)
            filename = params.get("filename") or "upload"
            rows = parse_upload(filename, read_limited_body(self, 10 * 1024 * 1024))
            preview = create_preview(BASE, rows, filename)
            append_event(
                BASE,
                "import.preview",
                "success",
                preview_id=preview["preview_id"],
                affected_count=len(rows),
            )
            self.send_admin_json(preview)
            return

        body = self.parse_admin_json()
        if path == "/api/admin/users":
            user = identity_store().create_user(body.get("display_name"), body.get("role", "viewer"), body.get("permissions"))
            issued = identity_store().issue_token(user["user_id"], body.get("expires_at"))
            self.send_admin_json({**user, **issued}, HTTPStatus.CREATED)
            return
        if path.startswith("/api/admin/users/") and path.endswith("/revoke"):
            user_id = path.split("/")[-2]
            count = identity_store().revoke_user_tokens(user_id)
            self.send_admin_json({"user_id": user_id, "revoked_tokens": count})
            return
        if path == "/api/admin/auth/verify":
            append_event(BASE, "auth.verify", "success", client=self.client_address[0])
            self.send_admin_json({"authenticated": True})
            return
        if path == "/api/admin/interfaces/test":
            job = admin_job_manager().start(
                "interface_test",
                payload={"interface_ids": body.get("interface_ids")},
            )
            append_event(BASE, "interfaces.test", "accepted", job_id=job["job_id"])
            self.send_admin_json(job, HTTPStatus.ACCEPTED)
            return
        if path == "/api/admin/companies/update":
            ticker = body.get("ticker")
            result = update_company(BASE, ticker, body.get("updates") or body, default_companies)
            append_event(BASE, "company.update", "success", ticker=ticker, affected_count=1)
            self.send_admin_json(result)
            return
        if path == "/api/admin/companies/toggle":
            ticker = body.get("ticker")
            result = toggle_company(BASE, ticker, bool(body.get("enabled")), default_companies)
            append_event(BASE, "company.toggle", "success", ticker=ticker, enabled=bool(body.get("enabled")), affected_count=1)
            self.send_admin_json(result)
            return
        if path == "/api/admin/import/apply":
            result = apply_preview(
                BASE,
                body.get("preview_id"),
                body.get("mode", "merge"),
                body.get("confirmation", ""),
                default_companies,
            )
            append_event(
                BASE,
                "import.apply",
                "success",
                preview_id=body.get("preview_id"),
                mode=result["mode"],
                backup_id=result.get("backup_id"),
                affected_count=result["applied"],
            )
            self.send_admin_json(result)
            return
        if path == "/api/admin/jobs/start":
            job = admin_job_manager().start(body.get("kind"), trigger="manual")
            append_event(BASE, "job.start", "accepted", job_id=job["job_id"], kind=job["kind"])
            self.send_admin_json(job, HTTPStatus.ACCEPTED)
            return
        raise ApiError(404, "unknown_admin_path", "Unknown admin API path")

    def handle_private_post(self, path, auth):
        workspace = WorkspaceContext(BASE, auth)
        if path == "/api/v1/me/interfaces/import/preview":
            params = query_params(self.path)
            filename = params.get("filename") or "upload"
            rows = parse_private_interface_upload(filename, read_limited_body(self, 10 * 1024 * 1024))
            self.send_admin_json(create_private_interface_preview(workspace, rows, filename))
            return
        body = self.parse_admin_json()
        if path == "/api/v1/me/watchlist/add":
            self.send_admin_json(add_personal_tickers(workspace, body.get("items") or []))
            return
        if path == "/api/v1/me/watchlist/remove":
            self.send_admin_json(remove_personal_tickers(workspace, body.get("tickers") or []))
            return
        if path == "/api/v1/me/interfaces":
            self.send_admin_json(create_private_interface(workspace, body), HTTPStatus.CREATED)
            return
        if path.startswith("/api/v1/me/interfaces/") and path.endswith("/credentials"):
            interface_id=path.split("/")[-2]
            require_permission(auth,"interfaces.write"); get_private_interface(workspace,interface_id)
            auth_type,bundle=credential_bundle(body)
            KeychainStore(auth).put(interface_id,bundle)
            record=update_private_interface(workspace,interface_id,{"auth_type":auth_type,"credential_configured":True})
            self.send_admin_json({"id":record["id"],"auth_type":auth_type,"credential_configured":True})
            return
        if path == "/api/v1/me/interfaces/import/apply":
            self.send_admin_json(apply_private_interface_preview(workspace, body.get("preview_id")))
            return
        if path.startswith("/api/v1/me/interfaces/") and path.endswith("/test"):
            interface_id = path.split("/")[-2]
            require_permission(auth, "interfaces.test")
            record = get_private_interface(workspace, interface_id)
            result = probe_interface(BASE, record)
            InterfaceResultStore(workspace).record(interface_id, result)
            self.send_admin_json(result)
            return
        if path == "/api/v1/me/support-grants":
            result = identity_store().create_support_grant(
                auth, body.get("administrator_id", "owner"), body.get("scopes"),
                body.get("reason"), body.get("expires_at"),
            )
            self.send_admin_json(result, HTTPStatus.CREATED)
            return
        if path.startswith("/api/v1/me/support-grants/") and path.endswith("/revoke"):
            grant_id = path.split("/")[-2]
            if not identity_store().revoke_support_grant(auth, grant_id):
                raise ApiError(404, "grant_not_found", "Support grant not found")
            self.send_admin_json({"grant_id": grant_id, "revoked": True})
            return
        raise ApiError(404, "unknown_private_path", "Unknown private API path")

    def handle_add(self, body):
        raw_items = body.get("items")
        if raw_items is None:
            raw_items = [{"ticker": t, "name": ""} for t in body.get("tickers", [])]
        success, failed = [], []
        resolved = {}
        for item in raw_items:
            ticker = item.get("ticker") if isinstance(item, dict) else item
            name = item.get("name") if isinstance(item, dict) else ""
            try:
                t = normalize_ticker(ticker)
                row = resolve_ticker(BASE, t, override_name=name)
                row["source"] = "user_existing" if row.get("source") == "existing" else "user_auto"
                resolved[t] = row
                success.append({"ticker": t, "name": row.get("name"), "location_source": row.get("location_source")})
            except Exception as exc:
                failed.append({"ticker": str(ticker or "").strip(), "reason": str(exc)})
        with data_lock(BASE):
            user = load_user_watchlist(BASE)
            user.setdefault("add", {}).update(resolved)
            remove = set(user.get("remove", []))
            remove.difference_update(resolved.keys())
            user["remove"] = sorted(remove)
            write_user_watchlist(BASE, user)
            active = rebuild_active_universe(BASE, default_companies())
        self.send_json({"ok": not failed, "data": {"success": success, "failed": failed, "active": active.get("counts", {})}})

    def handle_remove(self, body):
        tickers = [normalize_ticker(t) for t in body.get("tickers", [])]
        with data_lock(BASE):
            user = load_user_watchlist(BASE)
            add = user.setdefault("add", {})
            remove = set(user.get("remove", []))
            for ticker in tickers:
                if ticker in add:
                    add.pop(ticker, None)
                else:
                    remove.add(ticker)
            user["remove"] = sorted(remove)
            write_user_watchlist(BASE, user)
            active = rebuild_active_universe(BASE, default_companies())
        self.send_json({"ok": True, "data": {"removed": tickers, "active": active.get("counts", {})}})

    def handle_clear_base(self, body):
        restore = bool(body.get("restore"))
        with data_lock(BASE):
            user = load_user_watchlist(BASE)
            user["replace_base"] = not restore
            write_user_watchlist(BASE, user)
            active = rebuild_active_universe(BASE, default_companies())
        self.send_json({"ok": True, "data": {"replace_base": user["replace_base"], "active": active.get("counts", {})}})

    def handle_update_meta(self, body):
        ticker = normalize_ticker(body.get("ticker"))
        updates = {k: v for k, v in body.items() if k in {"name", "country", "city", "lat", "lon", "seg", "chain", "chain_key"}}
        with data_lock(BASE):
            user = load_user_watchlist(BASE)
            add = user.setdefault("add", {})
            if ticker not in add:
                add[ticker] = resolve_ticker(BASE, ticker)
            add[ticker].update(updates)
            add[ticker]["source"] = "manual"
            write_user_watchlist(BASE, user)
            active = rebuild_active_universe(BASE, default_companies())
        self.send_json({"ok": True, "data": {"ticker": ticker, "active": active.get("counts", {})}})


class FastThreadingHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 128

    def __init__(self, server_address, RequestHandlerClass, bind_and_activate=True, concurrency_slots=None):
        self.max_concurrency = max(4, min(int(os.getenv("AICM_MAX_CONCURRENCY", "24")), 256))
        self.connection_timeout = max(5, min(int(os.getenv("AICM_CONNECTION_TIMEOUT", "30")), 300))
        self._slots = concurrency_slots or threading.BoundedSemaphore(self.max_concurrency)
        super().__init__(server_address, RequestHandlerClass, bind_and_activate)

    def server_bind(self):
        self.socket.bind(self.server_address)
        self.server_address = self.socket.getsockname()
        self.server_name = str(self.server_address[0])
        self.server_port = int(self.server_address[1])

    def get_request(self):
        request, address = super().get_request()
        request.settimeout(self.connection_timeout)
        return request, address

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            body = b'{"ok":false,"message":"service busy","errors":[]}'
            response = (
                b"HTTP/1.1 503 Service Unavailable\r\n"
                b"Content-Type: application/json; charset=utf-8\r\n"
                + f"Content-Length: {len(body)}\r\n".encode("ascii")
                + b"Retry-After: 2\r\nConnection: close\r\n\r\n"
                + body
            )
            try: request.sendall(response)
            except OSError: pass
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


def main():
    host = os.getenv("AICM_BIND_HOST", "127.0.0.1")
    port = int(os.getenv("AICM_PORT", "8911"))
    loopback_port = int(os.getenv("AICM_LOOPBACK_PORT", "0") or 0)
    ensure_admin_token()
    if not enabled("AICM_HTTP11", True):
        AppHandler.protocol_version = "HTTP/1.0"
    os.chdir(BASE)
    slots = threading.BoundedSemaphore(max(4, min(int(os.getenv("AICM_MAX_CONCURRENCY", "24")), 256)))
    httpd = FastThreadingHTTPServer((host, port), AppHandler, concurrency_slots=slots)
    loopback = None
    loopback_thread = None
    if loopback_port:
        try:
            loopback = FastThreadingHTTPServer(("127.0.0.1", loopback_port), AppHandler, concurrency_slots=slots)
        except Exception:
            httpd.server_close()
            raise
        loopback_thread = threading.Thread(target=loopback.serve_forever, name="aicm-loopback-http", daemon=True)
        loopback_thread.start()
    monitor = private_monitor_runtime()
    listeners = [f"http://{host}:{port}/"]
    if loopback: listeners.append(f"http://127.0.0.1:{loopback_port}/")
    print(f"AICM serving {', '.join(listeners)} from {BASE}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("AICM shutdown requested")
    finally:
        if loopback:
            loopback.shutdown(); loopback.server_close()
            if loopback_thread: loopback_thread.join(timeout=5)
        monitor.stop(); _PRIVATE_MONITORS.pop(str(BASE.resolve()),None); httpd.server_close()


if __name__ == "__main__":
    main()
