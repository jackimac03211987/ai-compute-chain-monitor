# -*- coding: utf-8 -*-
"""Read models and catalog operations for the admin console."""

import datetime
import importlib.util
import json
import time
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

from admin_common import ApiError, now_iso
from aicm_io import data_lock, read_json
from ticker_meta import exchange_of_ticker, normalize_ticker, resolve_ticker
from watchlist_loader import (
    load_active_universe,
    load_user_watchlist,
    rebuild_active_universe,
    write_user_watchlist,
)


INTERFACE_IDS = (
    "web_api",
    "yahoo_quote",
    "yahoo_fx",
    "live_task",
    "history_task",
    "catalog",
    "browser_json",
    "transfer_engine",
)
PAGE_SIZES = {25, 50, 100}
REQUIRED_COMPANY_FIELDS = ("t", "name", "country", "city", "lat", "lon", "seg")


def _as_items(payload):
    items = payload.get("items", {}) if isinstance(payload, dict) else {}
    if isinstance(items, dict):
        return items
    if isinstance(items, list):
        return {str(row.get("t") or "").upper(): row for row in items if row.get("t")}
    return {}


def _parse_day(value):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.date.fromisoformat(text[:10])
    except ValueError:
        return None


def _history_state(payload, status_payload=None, today=None):
    today = today or datetime.date.today()
    companies = payload.get("companies", []) if isinstance(payload, dict) else []
    dates = payload.get("dates", []) if isinstance(payload, dict) else []
    meta = payload.get("meta", {}) if isinstance(payload, dict) else {}
    fetched = _parse_day(meta.get("fetched"))
    age_days = (today - fetched).days if fetched else None
    status_payload = status_payload or {}
    raw_task_status = str(status_payload.get("status") or "").lower()
    if raw_task_status in {"error", "failed"}:
        status = "failed"
        error = str(status_payload.get("last_error") or status_payload.get("message") or "Historical refresh failed")
        message = error + ("; retained historical data remains available" if companies and dates else "")
    elif raw_task_status == "running":
        status = "running"
        message = "Historical refresh is running"
    elif not companies or not dates:
        status = "failed"
        message = "No valid historical window is available"
    elif age_days is None or age_days > 3:
        status = "stale"
        message = "Historical window is older than three days"
    else:
        status = "healthy"
        message = "Historical window is current"
    return {
        "status": status,
        "message": message,
        "fetched": meta.get("fetched"),
        "age_days": age_days,
        "company_count": len(companies),
        "date_count": len(dates),
        "task_status": raw_task_status or None,
        "last_attempt": status_payload.get("last_attempt") or status_payload.get("started_at"),
        "last_success": status_payload.get("last_success") or meta.get("fetched"),
        "retained_data_available": bool(companies and dates),
    }


def build_overview(base, health_probe=None):
    base = Path(base)
    active = read_json(base / "data" / "active_universe.json", {})
    live = read_json(base / "data" / "live.json", {})
    live_status = read_json(base / "data" / "live_status.json", {})
    history = read_json(base / "data" / "semi_market.json", {})
    history_status = read_json(base / "data" / "history_status.json", {})
    jobs = read_json(base / "data" / "admin_jobs.json", {"jobs": []})

    expected = int(live_status.get("expected_count") or len(active.get("companies", [])))
    fresh = int(live_status.get("fresh_count") or len(_as_items(live)))
    failed = max(0, expected - fresh)
    provider_failure_events = int(live_status.get("failed_count") or failed)
    raw_live_state = str(live_status.get("status") or "unknown")
    if fresh > 0 and raw_live_state in {"success", "running", "busy"}:
        quote_state = "healthy"
    elif fresh > 0:
        quote_state = "warning"
    else:
        quote_state = "failed"

    history_state = _history_state(history, history_status)
    attention_items = []
    if failed:
        attention_items.append({
            "id": "missing_quotes",
            "severity": "warning",
            "count": failed,
            "message": f"{failed} companies have no fresh quote",
        })
    if history_state["status"] != "healthy":
        attention_items.append({
            "id": "history_status",
            "severity": "error" if history_state["status"] == "failed" else "warning",
            "count": 1,
            "message": history_state["message"],
        })

    service_probe = health_probe() if health_probe else {"status": "healthy", "latency_ms": None}
    active_jobs = [
        row for row in jobs.get("jobs", [])
        if row.get("status") in {"queued", "running"}
    ] if isinstance(jobs, dict) else []

    return {
        "generated_at": now_iso(),
        "service": service_probe,
        "catalog": {
            "active": len(active.get("companies", [])),
            "counts": active.get("counts", {}),
        },
        "quotes": {
            "status": quote_state,
            "raw_status": raw_live_state,
            "fresh": fresh,
            "expected": expected,
            "failed": failed,
            "provider_failure_events": provider_failure_events,
            "coverage_pct": round((fresh / expected * 100), 1) if expected else 0,
            "last_success": live_status.get("last_success") or live.get("asof"),
            "elapsed_s": live_status.get("elapsed_s"),
            "provider": live_status.get("data_provider") or "yahoo_chart",
        },
        "history": history_state,
        "attention": {
            "count": sum(int(item.get("count") or 0) for item in attention_items),
            "items": attention_items,
        },
        "active_jobs": active_jobs,
    }


def list_interfaces(base, probes=None):
    base = Path(base)
    overview = build_overview(base)
    live_status = read_json(base / "data" / "live_status.json", {})
    active = read_json(base / "data" / "active_universe.json", {})
    live = read_json(base / "data" / "live.json", {})
    transfer_ready = importlib.util.find_spec("openpyxl") is not None
    history = overview["history"]
    quote_status = overview["quotes"]["status"]
    live_task_status = quote_status if live_status.get("last_success") else "failed"
    browser_json_status = "healthy" if _as_items(live) and live_status else "failed"
    catalog_status = "healthy" if active.get("companies") else "failed"

    items = [
        {
            "id": "web_api", "category": "service", "label": "Web / API service",
            "provider": "AICM", "purpose": "Serve the dashboard and admin APIs",
            "status": "healthy", "last_success": now_iso(), "latency_ms": None,
            "coverage": "8911", "frequency": "continuous", "delay_grade": "local",
            "error": "",
        },
        {
            "id": "yahoo_quote", "category": "provider", "label": "Yahoo Chart quotes",
            "provider": "Yahoo Finance chart", "purpose": "Equity price and previous close",
            "status": quote_status, "last_success": overview["quotes"]["last_success"],
            "latency_ms": None, "coverage": f'{overview["quotes"]["fresh"]}/{overview["quotes"]["expected"]}',
            "frequency": "180 seconds", "delay_grade": "near-real-time", "error": "",
        },
        {
            "id": "yahoo_fx", "category": "provider", "label": "Yahoo FX conversion",
            "provider": "Yahoo Finance chart", "purpose": "Convert local currencies to USD",
            "status": quote_status, "last_success": overview["quotes"]["last_success"],
            "latency_ms": None, "coverage": "19 currencies", "frequency": "with quote refresh",
            "delay_grade": "near-real-time", "error": "",
        },
        {
            "id": "live_task", "category": "task", "label": "Live refresh task",
            "provider": "fetch_live.py", "purpose": "Build the latest quote snapshot",
            "status": live_task_status, "last_success": overview["quotes"]["last_success"],
            "latency_ms": round(float(live_status.get("elapsed_s") or 0) * 1000, 1),
            "coverage": f'{overview["quotes"]["fresh"]}/{overview["quotes"]["expected"]}',
            "frequency": f'{live_status.get("refresh_seconds") or 180} seconds',
            "delay_grade": "scheduled", "error": str(live_status.get("last_error") or ""),
        },
        {
            "id": "history_task", "category": "task", "label": "Historical-window task",
            "provider": "fetch_data.py", "purpose": "Build the rolling historical window",
            "status": history["status"], "last_success": history.get("fetched"),
            "latency_ms": None, "coverage": f'{history["company_count"]} companies',
            "frequency": "daily 07:30", "delay_grade": "daily", "error": history["message"],
        },
        {
            "id": "catalog", "category": "data", "label": "Effective company catalog",
            "provider": "watchlist_loader.py", "purpose": "Build the active universe",
            "status": catalog_status, "last_success": active.get("generated_at"),
            "latency_ms": None, "coverage": f'{len(active.get("companies", []))} companies',
            "frequency": "on catalog change", "delay_grade": "local", "error": "",
        },
        {
            "id": "browser_json", "category": "data", "label": "Browser quote payloads",
            "provider": "live.json / live_status.json", "purpose": "Feed the LIVE browser view",
            "status": browser_json_status, "last_success": live.get("asof"),
            "latency_ms": None, "coverage": f'{len(_as_items(live))} quotes',
            "frequency": "browser polls every 15 seconds", "delay_grade": "local cache", "error": "",
        },
        {
            "id": "transfer_engine", "category": "data", "label": "Import / export engine",
            "provider": "openpyxl / csv / json", "purpose": "Validate and transfer catalogs",
            "status": "healthy" if transfer_ready else "failed", "last_success": None,
            "latency_ms": None, "coverage": "XLSX, CSV, JSON", "frequency": "on demand",
            "delay_grade": "local", "error": "" if transfer_ready else "openpyxl is unavailable",
        },
    ]
    if probes:
        for item in items:
            override = probes.get(item["id"])
            if override:
                item.update(override() if callable(override) else override)
    return items


def _chart_probe(symbol, timeout):
    encoded = urllib.parse.quote(symbol, safe="")
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded}?range=5d&interval=1d"
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read())
    results = ((payload.get("chart") or {}).get("result") or [])
    if not results:
        raise ValueError("Yahoo returned no chart result")
    return {"status": "healthy", "coverage": symbol}


def test_interfaces(base, interface_ids=None, timeout=8, probes=None):
    selected = set(interface_ids or INTERFACE_IDS)
    unknown = selected.difference(INTERFACE_IDS)
    if unknown:
        raise ApiError(400, "unknown_interface", f"Unknown interface: {sorted(unknown)[0]}")
    inventory = {row["id"]: row for row in list_interfaces(base)}
    results = []
    for interface_id in INTERFACE_IDS:
        if interface_id not in selected:
            continue
        started = time.monotonic()
        try:
            if probes and interface_id in probes:
                result = probes[interface_id]()
            elif interface_id == "yahoo_quote":
                result = _chart_probe("NVDA", timeout)
            elif interface_id == "yahoo_fx":
                result = _chart_probe("CNY=X", timeout)
            else:
                result = {"status": inventory[interface_id]["status"]}
            status = result.get("status", "healthy")
            error = ""
        except Exception as exc:
            status = "failed"
            error = str(exc)[:240]
        results.append({
            "id": interface_id,
            "status": status,
            "latency_ms": round((time.monotonic() - started) * 1000, 1),
            "tested_at": now_iso(),
            "error": error,
        })
    return {"items": results, "summary": dict(Counter(row["status"] for row in results))}


def _catalog_rows(base):
    base = Path(base)
    watchlist = read_json(base / "data" / "watchlist.json", {})
    user = load_user_watchlist(base)
    active = load_active_universe(base)
    live = read_json(base / "data" / "live.json", {})
    live_items = _as_items(live)
    active_map = {str(row.get("t") or "").upper(): dict(row) for row in active.get("companies", [])}
    removed = {str(t).upper() for t in user.get("remove", [])}

    rows = {}
    for row in watchlist.get("companies", []):
        ticker = str(row.get("t") or "").upper()
        if ticker:
            rows[ticker] = {**row, "t": ticker, "source": "base"}
    for ticker, row in user.get("add", {}).items():
        ticker = str(ticker).upper()
        rows[ticker] = {**rows.get(ticker, {}), **row, "t": ticker, "source": row.get("source") or "user"}
    for ticker, row in active_map.items():
        rows[ticker] = {**rows.get(ticker, {}), **row, "t": ticker}

    output = []
    for ticker in sorted(rows):
        row = dict(rows[ticker])
        quote = live_items.get(ticker)
        effective = ticker in active_map and ticker not in removed
        metadata_missing = [field for field in REQUIRED_COMPANY_FIELDS if row.get(field) in (None, "")]
        if not effective:
            status = "removed"
        elif metadata_missing:
            status = "metadata_issue"
        elif not quote:
            status = "missing_quote"
        elif quote.get("stale"):
            status = "stale_quote"
        else:
            status = "active"
        row.update({
            "exchange": exchange_of_ticker(ticker),
            "enabled": effective,
            "status": status,
            "quote_state": "missing" if not quote else ("stale" if quote.get("stale") else "fresh"),
            "latest_price": quote.get("p") if quote else None,
            "change": quote.get("chg") if quote else None,
            "quote_time": quote.get("market_time") if quote else None,
            "quote_source": quote.get("source") if quote else None,
            "metadata_missing": metadata_missing,
        })
        output.append(row)
    return output


def _facet(rows, field):
    counts = Counter(str(row.get(field) or "未分类") for row in rows)
    return [{"value": key, "count": value} for key, value in sorted(counts.items())]


def _param_int(params, name, default):
    try:
        return int(params.get(name, default))
    except (TypeError, ValueError):
        return default


def query_companies(base, params=None):
    params = params or {}
    rows = _catalog_rows(base)
    q = str(params.get("q") or "").strip().lower()
    if q:
        fields = ("t", "name", "name_zh", "name_en", "country", "country_en", "city", "seg", "chain")
        rows = [row for row in rows if any(q in str(row.get(field) or "").lower() for field in fields)]

    filter_map = {
        "country": "country", "exchange": "exchange", "source": "source",
        "status": "status", "seg": "seg",
    }
    for param, field in filter_map.items():
        value = str(params.get(param) or "").strip().lower()
        if value:
            rows = [row for row in rows if str(row.get(field) or "").lower() == value]

    sort_key = str(params.get("sort") or "t")
    if sort_key not in {"t", "name", "country", "city", "seg", "source", "status", "latest_price", "change", "quote_time"}:
        raise ApiError(400, "invalid_sort", "Unsupported company sort field")
    reverse = str(params.get("direction") or "asc").lower() == "desc"
    rows.sort(key=lambda row: (row.get(sort_key) is None, str(row.get(sort_key) or "").lower()), reverse=reverse)

    total = len(rows)
    page = max(1, _param_int(params, "page", 1))
    page_size = _param_int(params, "page_size", 50)
    if page_size not in PAGE_SIZES:
        page_size = 50
    start = (page - 1) * page_size
    items = rows[start:start + page_size]
    return {
        "items": items,
        "page": page,
        "page_size": page_size,
        "total": total,
        "facets": {
            "countries": _facet(rows, "country"),
            "exchanges": _facet(rows, "exchange"),
            "sources": _facet(rows, "source"),
            "statuses": _facet(rows, "status"),
            "segments": _facet(rows, "seg"),
        },
    }


def _find_company(payload, ticker):
    for row in payload.get("companies", []):
        if str(row.get("t") or "").upper() == ticker:
            return dict(row)
    return None


def update_company(base, ticker, updates, default_companies):
    base = Path(base)
    ticker = normalize_ticker(ticker)
    allowed = {"name", "country", "city", "lat", "lon", "seg", "chain", "chain_key"}
    clean_updates = {
        key: value for key, value in (updates or {}).items()
        if key in allowed and value not in (None, "")
    }
    current = _find_company(load_active_universe(base), ticker)
    resolved = current or resolve_ticker(base, ticker)
    with data_lock(base):
        user = load_user_watchlist(base)
        add = user.setdefault("add", {})
        row = dict(add.get(ticker) or resolved)
        row.update(clean_updates)
        row["t"] = ticker
        row["source"] = "manual"
        add[ticker] = row
        remove = {str(t).upper() for t in user.get("remove", [])}
        remove.discard(ticker)
        user["remove"] = sorted(remove)
        write_user_watchlist(base, user)
        active = rebuild_active_universe(base, default_companies())
    company = _find_company(active, ticker)
    return {"ticker": ticker, "company": company, "counts": active.get("counts", {})}


def toggle_company(base, ticker, enabled, default_companies):
    base = Path(base)
    ticker = normalize_ticker(ticker)
    watchlist = read_json(base / "data" / "watchlist.json", {})
    base_tickers = {str(row.get("t") or "").upper() for row in watchlist.get("companies", [])}
    resolved = None
    if enabled and ticker not in base_tickers:
        resolved = _find_company(load_active_universe(base), ticker) or resolve_ticker(base, ticker)
    with data_lock(base):
        user = load_user_watchlist(base)
        add = user.setdefault("add", {})
        remove = {str(t).upper() for t in user.get("remove", [])}
        if enabled:
            remove.discard(ticker)
            if ticker not in base_tickers and ticker not in add:
                add[ticker] = {**resolved, "t": ticker, "source": "manual"}
        else:
            if ticker in add and ticker not in base_tickers:
                add.pop(ticker, None)
            else:
                remove.add(ticker)
        user["remove"] = sorted(remove)
        write_user_watchlist(base, user)
        active = rebuild_active_universe(base, default_companies())
    return {"ticker": ticker, "enabled": bool(enabled), "counts": active.get("counts", {})}
