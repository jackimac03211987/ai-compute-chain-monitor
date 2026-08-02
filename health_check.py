# -*- coding: utf-8 -*-
"""Health checks for the AI compute chain dashboard."""

import argparse
import json
import math
import sys
import urllib.error
import urllib.request
from pathlib import Path


def read_json(base, path):
    with urllib.request.urlopen(base.rstrip("/") + "/" + path, timeout=10) as resp:
        return json.load(resp)


def read_text(base, path=""):
    with urllib.request.urlopen(base.rstrip("/") + "/" + path, timeout=10) as resp:
        return resp.read().decode("utf-8", "ignore")


def read_json_with_headers(base, path, headers=None):
    request = urllib.request.Request(
        base.rstrip("/") + "/" + path.lstrip("/"),
        headers=headers or {},
    )
    with urllib.request.urlopen(request, timeout=10) as resp:
        return json.load(resp)


def check_admin(base, token=""):
    errors = []
    static_available = False
    unauthorized_forbidden = False
    interface_count = 0
    overview_available = False
    private_static_available = False
    private_unauthorized_forbidden = False

    try:
        admin_html = read_text(base, "admin/")
        static_available = "AI 算力链运营控制台" in admin_html
        if not static_available:
            errors.append("admin static page marker missing")
    except Exception as exc:
        errors.append(f"admin static page unavailable: {exc}")

    try:
        private_html = read_text(base, "private/")
        private_static_available = "私有工作空间" in private_html
        if not private_static_available:
            errors.append("private static page marker missing")
    except Exception as exc:
        errors.append(f"private static page unavailable: {exc}")

    try:
        read_json(base, "api/v1/me/profile")
        errors.append("private profile without token unexpectedly succeeded")
    except urllib.error.HTTPError as exc:
        payload = {}
        try:
            payload = json.loads(exc.read().decode("utf-8"))
        except Exception:
            pass
        private_unauthorized_forbidden = (
            exc.code == 403 and payload.get("error", {}).get("code") == "personal_token_required"
        )
        if not private_unauthorized_forbidden:
            errors.append(f"private profile without token returned HTTP {exc.code}")
    except Exception as exc:
        errors.append(f"private unauthorized check failed: {exc}")

    try:
        read_json(base, "api/admin/overview")
        errors.append("admin overview without token unexpectedly succeeded")
    except urllib.error.HTTPError as exc:
        payload = {}
        try:
            payload = json.loads(exc.read().decode("utf-8"))
        except Exception:
            pass
        unauthorized_forbidden = (
            exc.code == 403
            and payload.get("error", {}).get("code") == "admin_token_required"
        )
        if not unauthorized_forbidden:
            errors.append(f"admin overview without token returned HTTP {exc.code}")
    except Exception as exc:
        errors.append(f"admin unauthorized check failed: {exc}")

    if token:
        headers = {"X-AICM-Admin-Token": token}
        try:
            overview = read_json_with_headers(base, "api/admin/overview", headers)
            overview_available = bool(overview.get("ok") and overview.get("data"))
            if not overview_available:
                errors.append("authorized admin overview returned invalid payload")
        except Exception as exc:
            errors.append(f"authorized admin overview unavailable: {exc}")
        try:
            interfaces = read_json_with_headers(base, "api/admin/interfaces", headers)
            interface_count = len(interfaces.get("data", {}).get("items", []))
            if interface_count != 8:
                errors.append(f"admin interface count mismatch: {interface_count}/8")
        except Exception as exc:
            errors.append(f"authorized admin interfaces unavailable: {exc}")
    else:
        errors.append("admin token unavailable for authorized health checks")

    return {
        "admin_static_available": static_available,
        "admin_unauthorized_forbidden": unauthorized_forbidden,
        "admin_overview_available": overview_available,
        "admin_interface_count": interface_count,
        "private_static_available": private_static_available,
        "private_unauthorized_forbidden": private_unauthorized_forbidden,
        "errors": errors,
    }


def expect_post_forbidden(base):
    url = base.rstrip("/") + "/api/watchlist/add"
    body = json.dumps({"items": [{"ticker": "AICMHEALTHCHECK"}]}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(req, timeout=10)
        return False, "POST without token unexpectedly succeeded"
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8"))
        except Exception:
            payload = {}
        if exc.code in (401, 403) and "admin token required" in str(payload.get("message", "")):
            return True, ""
        return False, f"POST without token returned HTTP {exc.code}: {payload}"


def audit_stock_data(active_companies, live_items, extreme_threshold=20.0):
    missing = []
    bad_values = []
    non_chart_sources = []
    extreme_moves = []

    for row in active_companies:
        ticker = row.get("t")
        if not ticker:
            continue
        item = live_items.get(ticker)
        if not item:
            missing.append(ticker)
            continue

        price = item.get("p")
        chg = item.get("chg")
        if not isinstance(price, (int, float)) or not math.isfinite(price) or price <= 0:
            bad_values.append({"ticker": ticker, "field": "p", "value": price})
        if not isinstance(chg, (int, float)) or not math.isfinite(chg):
            bad_values.append({"ticker": ticker, "field": "chg", "value": chg})
        elif abs(chg) >= extreme_threshold:
            extreme_moves.append({
                "ticker": ticker,
                "chg": chg,
                "p": price,
                "d": item.get("d"),
            })
        if item.get("source") != "yahoo_chart":
            non_chart_sources.append({"ticker": ticker, "source": item.get("source")})

    return {
        "missing": missing,
        "bad_values": bad_values,
        "non_chart_sources": non_chart_sources,
        "extreme_moves": sorted(extreme_moves, key=lambda item: abs(item["chg"]), reverse=True),
    }


def check(base):
    errors = []
    html = read_text(base)
    if "AI COMPUTE CHAIN PULSE" not in html:
        errors.append("HTML title marker missing")
    for marker in ("FX_A2", "customLayerData(FX_A2", "THREE.InstancedMesh", "A2EnergyLayer"):
        if marker not in html:
            errors.append(f"A2 marker missing from HTML: {marker}")

    health = read_json(base, "api/health")
    market = read_json(base, "data/semi_market.json")
    active = read_json(base, "data/active_universe.json")
    live = read_json(base, "data/live.json")
    status = read_json(base, "data/live_status.json")

    companies = market.get("companies", [])
    active_companies = active.get("companies", [])
    chain_fields = sum(1 for c in companies if c.get("chain_key"))
    if not companies:
        errors.append("semi_market.json has no companies")
    if chain_fields != len(companies):
        errors.append(f"chain field coverage mismatch: {chain_fields}/{len(companies)}")
    if not active_companies:
        errors.append("active_universe.json has no companies")
    if health.get("active_universe_count") != len(active_companies):
        errors.append(f"active universe count mismatch: health={health.get('active_universe_count')} file={len(active_companies)}")

    live_items = live.get("items", {})
    expected_live = len(active_companies) or len(companies)
    if len(live_items) < max(1, int(expected_live * 0.7)):
        errors.append(f"live item count too low: {len(live_items)}/{expected_live}")
    if status.get("stale_count", 0):
        errors.append(f"live stale_count is nonzero: {status.get('stale_count')}")

    if status.get("status") not in {"success", "running", "busy"}:
        errors.append(f"unexpected live status: {status.get('status')}")

    stock_audit = audit_stock_data(active_companies, live_items)
    if stock_audit["bad_values"]:
        errors.append(f"stock data has invalid values: {stock_audit['bad_values'][:5]}")
    if stock_audit["non_chart_sources"]:
        errors.append(f"unexpected stock data sources: {stock_audit['non_chart_sources'][:5]}")
    max_expected_extremes = max(5, int(len(live_items) * 0.05))
    if len(stock_audit["extreme_moves"]) > max_expected_extremes:
        errors.append(
            f"too many extreme stock moves >=20%: {len(stock_audit['extreme_moves'])}/{len(live_items)}; "
            f"sample={stock_audit['extreme_moves'][:5]}"
        )

    provider = status.get("data_provider") or live.get("meta", {}).get("data_provider")
    if not provider:
        errors.append("live data provider metadata missing")

    forbidden_ok, forbidden_error = expect_post_forbidden(base)
    if not forbidden_ok:
        errors.append(forbidden_error)

    return {
        "base": base,
        "companies": len(companies),
        "active_universe": len(active_companies),
        "chain_fields": chain_fields,
        "live_items": len(live_items),
        "live_asof": live.get("asof"),
        "status": status.get("status"),
        "last_success": status.get("last_success"),
        "fresh_count": status.get("fresh_count"),
        "stale_count": status.get("stale_count"),
        "live_missing_count": len(stock_audit["missing"]),
        "live_missing_sample": stock_audit["missing"][:10],
        "stock_bad_value_count": len(stock_audit["bad_values"]),
        "extreme_move_count": len(stock_audit["extreme_moves"]),
        "extreme_move_sample": stock_audit["extreme_moves"][:10],
        "data_provider": provider,
        "poll_seconds": status.get("poll_seconds"),
        "refresh_seconds": status.get("refresh_seconds"),
        "post_without_token_forbidden": forbidden_ok,
        "a2_markers_present": all(marker in html for marker in ("FX_A2", "customLayerData(FX_A2", "THREE.InstancedMesh", "A2EnergyLayer")),
        "errors": errors,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8911")
    ap.add_argument("--admin-token-file", default="")
    args = ap.parse_args()
    result = check(args.base)
    token_path = Path(args.admin_token_file) if args.admin_token_file else Path(__file__).parent / "data" / "admin_token.txt"
    token = token_path.read_text(encoding="utf-8").strip() if token_path.exists() else ""
    admin = check_admin(args.base, token)
    result["errors"].extend(admin.pop("errors"))
    result.update(admin)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["errors"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
