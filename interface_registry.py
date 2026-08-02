# -*- coding: utf-8 -*-
"""Unified built-in and custom interface records scoped to one workspace."""
import hashlib, json, os, secrets, threading
from copy import deepcopy
from admin_common import ApiError, now_iso
from auth_context import require_permission


BUILTIN_DEFAULTS = [
    ("web_api", "Web / API service", "service", "local_task", 1),
    ("yahoo_quote", "Yahoo Chart quotes", "provider", "composite", 3),
    ("yahoo_fx", "Yahoo FX conversion", "provider", "composite", 3),
    ("live_task", "Live refresh task", "task", "local_task", 3),
    ("history_task", "Historical-window task", "task", "local_task", 1440),
    ("catalog", "Effective company catalog", "data", "local_file", 60),
    ("browser_json", "Browser JSON data", "data", "local_file", 3),
    ("transfer_engine", "Import / export engine", "service", "local_task", 60),
]
MODES = {"http", "local_task", "local_file", "composite"}
_QUOTA_LOCK = threading.RLock()


def builtin_defaults():
    return [{"id": i, "origin": "builtin", "enabled": True, "name": n, "provider": "AICM", "category": c, "purpose": n, "monitor_mode": m, "method": "GET", "url": "", "allow_private_target": False, "timeout_seconds": 8, "interval_minutes": interval, "expected_statuses": [200], "auth_type": "none", "credential_configured": False, "created_at": now_iso(), "updated_at": now_iso()} for i,n,c,m,interval in BUILTIN_DEFAULTS]


def _load(workspace):
    payload = workspace.read_json("interface_registry.json", {"version": 1, "interfaces": []})
    rows = payload.get("interfaces", []) if isinstance(payload, dict) else []
    by_id = {str(row.get("id")): dict(row) for row in rows if row.get("id")}
    changed = False
    for row in builtin_defaults():
        if row["id"] not in by_id: by_id[row["id"]] = row; changed = True
    payload = {"version": 1, "interfaces": list(by_id.values())}
    if changed: workspace.write_json("interface_registry.json", payload)
    return payload


def _save(workspace, payload): workspace.write_json("interface_registry.json", payload)
def _hash(payload): return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def list_records(workspace, params=None):
    require_permission(workspace.auth, "interfaces.read")
    payload = _load(workspace); rows = [deepcopy(x) for x in payload["interfaces"]]; params = params or {}
    q = str(params.get("q") or "").lower()
    if q: rows = [r for r in rows if q in " ".join(str(r.get(k,"")) for k in ("name","provider","purpose","url")).lower()]
    for key in ("origin","category","monitor_mode"):
        if params.get(key): rows = [r for r in rows if r.get(key) == params[key]]
    return {"items": rows, "total": len(rows), "workspace_hash": _hash(payload)}


def get_record(workspace, interface_id):
    for row in list_records(workspace)["items"]:
        if row["id"] == interface_id: return row
    raise ApiError(404, "interface_not_found", "Interface not found")


def _env_int(name, default, minimum, maximum):
    try: value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError): value = default
    return max(minimum, min(value, maximum))


def quota_config():
    return {
        "max_custom_per_user": _env_int("AICM_MAX_INTERFACES_PER_USER", 10, 1, 1000),
        "max_custom_global": _env_int("AICM_MAX_INTERFACES_GLOBAL", 50, 1, 10000),
        "min_custom_interval": _env_int("AICM_MIN_CUSTOM_INTERVAL", 15, 1, 1440),
        "max_custom_timeout": _env_int("AICM_MAX_CUSTOM_TIMEOUT", 15, 1, 30),
    }


def _bool(value):
    if isinstance(value, bool): return value
    if isinstance(value, (int, float)): return bool(value)
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _global_custom_count(base):
    total = 0
    root = base / "data" / "tenants"
    for path in root.glob("*/users/*/interface_registry.json") if root.exists() else ():
        try: payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception: continue
        total += sum(1 for row in payload.get("interfaces", []) if row.get("origin") == "custom")
    return total


def _clean(payload, current=None, auth=None):
    row = dict(current or {})
    allowed = {"name","provider","category","purpose","monitor_mode","method","url","allow_private_target","timeout_seconds","interval_minutes","expected_statuses","max_latency_ms","json_path","expected_value","body_keyword","freshness_json_path","max_age_minutes","headers","auth_type","credential_configured","notes","enabled"}
    for key,value in (payload or {}).items():
        if key in allowed and value not in (None, ""): row[key] = value
    if not str(row.get("name") or "").strip(): raise ApiError(400,"invalid_interface","Interface name is required")
    if row.get("monitor_mode") not in MODES: raise ApiError(400,"invalid_interface","Invalid monitor mode")
    row["method"] = str(row.get("method") or "GET").upper()
    if row["method"] not in {"GET","HEAD"}: raise ApiError(400,"invalid_interface","Method must be GET or HEAD")
    row["timeout_seconds"] = int(row.get("timeout_seconds") or 8); row["interval_minutes"] = int(row.get("interval_minutes") or 15)
    is_custom = row.get("origin", "custom") == "custom"
    limits = quota_config()
    min_interval = limits["min_custom_interval"] if is_custom else 1
    max_timeout = limits["max_custom_timeout"] if is_custom else 30
    if not 1 <= row["interval_minutes"] <= 1440 or row["interval_minutes"] < min_interval:
        raise ApiError(422,"invalid_interval",f"Custom interface interval must be {min_interval}-1440 minutes")
    if not 1 <= row["timeout_seconds"] <= max_timeout:
        raise ApiError(422,"invalid_timeout",f"Custom interface timeout must be 1-{max_timeout} seconds")
    row["allow_private_target"] = _bool(row.get("allow_private_target", False))
    if row["allow_private_target"] and (auth is None or auth.role != "owner"):
        raise ApiError(403,"private_target_forbidden","Private and Tailscale targets require owner authorization")
    row["updated_at"] = now_iso(); return row


def create_record(workspace, payload):
    return create_records(workspace, [payload])[0]


def create_records(workspace, payloads):
    require_permission(workspace.auth,"interfaces.write")
    rows = list(payloads or [])
    if not rows: return []
    with _QUOTA_LOCK:
        registry = _load(workspace)
        limits = quota_config()
        current_user = sum(1 for row in registry["interfaces"] if row.get("origin") == "custom")
        if current_user + len(rows) > limits["max_custom_per_user"]:
            raise ApiError(429,"quota_exceeded",f"Custom interface quota is {limits['max_custom_per_user']} per user")
        current_global = _global_custom_count(workspace.base)
        if current_global + len(rows) > limits["max_custom_global"]:
            raise ApiError(429,"global_quota_exceeded",f"Global custom interface quota is {limits['max_custom_global']}")
        created = []
        for payload in rows:
            row = _clean(payload, auth=workspace.auth)
            row.update({"id":"custom_"+secrets.token_hex(10),"origin":"custom","enabled":_bool(row.get("enabled",True)),"created_at":now_iso()})
            registry["interfaces"].append(row); created.append(deepcopy(row))
        _save(workspace,registry)
        return created


def update_record(workspace, interface_id, updates):
    require_permission(workspace.auth,"interfaces.write"); registry=_load(workspace)
    for index,row in enumerate(registry["interfaces"]):
        if row["id"]==interface_id: registry["interfaces"][index]=_clean(updates,row,workspace.auth); _save(workspace,registry); return deepcopy(registry["interfaces"][index])
    raise ApiError(404,"interface_not_found","Interface not found")


def reset_builtin(workspace, interface_id):
    require_permission(workspace.auth,"interfaces.write"); defaults={r["id"]:r for r in builtin_defaults()}
    if interface_id not in defaults: raise ApiError(409,"not_builtin","Only built-ins can be reset")
    registry=_load(workspace); registry["interfaces"]=[defaults[interface_id] if r["id"]==interface_id else r for r in registry["interfaces"]]; _save(workspace,registry); return deepcopy(defaults[interface_id])


def delete_record(workspace, interface_id):
    require_permission(workspace.auth,"interfaces.write"); registry=_load(workspace); row=get_record(workspace,interface_id)
    if row["origin"]=="builtin": raise ApiError(409,"builtin_delete_forbidden","Built-in interfaces cannot be deleted")
    registry["interfaces"]=[r for r in registry["interfaces"] if r["id"]!=interface_id]; _save(workspace,registry); return {"deleted":interface_id}
