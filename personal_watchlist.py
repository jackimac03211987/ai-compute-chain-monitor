# -*- coding: utf-8 -*-
"""Private watchlists over shared read-only market snapshots."""

from auth_context import require_permission
from ticker_meta import normalize_ticker


ALLOWED_FIELDS = {
    "name", "country", "city", "lat", "lon", "seg", "chain", "chain_key", "notes"
}


def _payload(workspace):
    payload = workspace.read_json("watchlist.json", {"version": 1, "items": {}})
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), dict):
        return {"version": 1, "items": {}}
    return payload


def _quotes(shared_quotes):
    if not isinstance(shared_quotes, dict):
        return {}
    items = shared_quotes.get("items")
    return items if isinstance(items, dict) else shared_quotes


def list_personal_watchlist(workspace, shared_quotes=None):
    require_permission(workspace.auth, "watchlist.read")
    payload = _payload(workspace)
    quotes = _quotes(shared_quotes or {})
    rows = []
    for ticker in sorted(payload["items"]):
        row = dict(payload["items"][ticker])
        row["ticker"] = ticker
        row["quote"] = dict(quotes.get(ticker) or {})
        rows.append(row)
    return {"items": rows, "count": len(rows)}


def add_personal_tickers(workspace, items):
    require_permission(workspace.auth, "watchlist.write")
    payload = _payload(workspace)
    added, updated = [], []
    for raw in items or []:
        if not isinstance(raw, dict):
            raw = {"ticker": raw}
        ticker = normalize_ticker(raw.get("ticker") or raw.get("t"))
        existing = dict(payload["items"].get(ticker) or {})
        row = dict(existing)
        for key in ALLOWED_FIELDS:
            if raw.get(key) not in (None, ""):
                row[key] = raw[key]
        row["ticker"] = ticker
        payload["items"][ticker] = row
        (updated if existing else added).append(ticker)
    workspace.write_json("watchlist.json", payload)
    return {"added": added, "updated": updated, "count": len(payload["items"])}


def update_personal_ticker(workspace, ticker, updates):
    require_permission(workspace.auth, "watchlist.write")
    ticker = normalize_ticker(ticker)
    payload = _payload(workspace)
    if ticker not in payload["items"]:
        raise ValueError("personal ticker not found")
    row = dict(payload["items"][ticker])
    for key in ALLOWED_FIELDS:
        if (updates or {}).get(key) not in (None, ""):
            row[key] = updates[key]
    row["ticker"] = ticker
    payload["items"][ticker] = row
    workspace.write_json("watchlist.json", payload)
    return {"item": row, "count": len(payload["items"])}


def remove_personal_tickers(workspace, tickers):
    require_permission(workspace.auth, "watchlist.write")
    payload = _payload(workspace)
    removed = []
    for raw in tickers or []:
        ticker = normalize_ticker(raw)
        if payload["items"].pop(ticker, None) is not None:
            removed.append(ticker)
    workspace.write_json("watchlist.json", payload)
    return {"removed": removed, "count": len(payload["items"])}
