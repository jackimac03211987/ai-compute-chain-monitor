# -*- coding: utf-8 -*-
"""Resolve user-entered tickers into dashboard company metadata."""

import re
import time
import json
import urllib.parse
import urllib.request
from pathlib import Path

from aicm_io import read_json, utc_ts, write_json_atomic
from watchlist_loader import _as_company_tuple, _clean_ticker, load_active_universe


CACHE_NAME = "ticker_meta_cache.json"
CACHE_SCHEMA_VERSION = 2
NEGATIVE_TTL_SECONDS = 24 * 60 * 60


def exchange_of_ticker(ticker):
    t = _clean_ticker(ticker)
    suffixes = [
        (".TW", "TWSE"), (".TWO", "TPEX"), (".KS", "KRX"), (".KQ", "KOSDAQ"),
        (".T", "TSE"), (".AS", "ENXAMS"), (".DE", "XETRA"), (".PA", "ENXPAR"),
        (".BR", "ENXBRU"), (".MC", "BME"), (".MI", "BIT"), (".L", "LSE"),
        (".OL", "OSL"), (".ST", "OMXSTO"), (".SW", "SIX"), (".HK", "HKEX"),
        (".SS", "SSE"), (".SZ", "SZSE"), (".BJ", "BSEBJ"), (".KL", "BURSA"),
        (".SI", "SGX"), (".SR", "TADAWUL"), (".NS", "NSE"), (".AX", "ASX"),
        (".V", "TSXV"), (".CL", "BVC"), (".JO", "JSE"),
    ]
    for suffix, exchange in suffixes:
        if t.endswith(suffix):
            return exchange
    return "US"


def normalize_ticker(value):
    ticker = _clean_ticker(value)
    if not re.match(r"^[0-9A-Z.\-]+$", ticker):
        raise ValueError("ticker contains unsupported characters")
    return ticker


def load_cache(base_dir):
    payload = read_json(Path(base_dir) / "data" / CACHE_NAME, {})
    return payload if isinstance(payload, dict) else {}


def write_cache(base_dir, payload):
    write_json_atomic(Path(base_dir) / "data" / CACHE_NAME, payload)


def cached_result(base_dir, ticker):
    cache = load_cache(base_dir)
    item = cache.get(ticker)
    if not item:
        return None
    if item.get("schema_version") != CACHE_SCHEMA_VERSION:
        return None
    if item.get("status") == "failed":
        resolved = item.get("resolved_epoch") or 0
        if time.time() - float(resolved) > NEGATIVE_TTL_SECONDS:
            return None
    return item


def remember(base_dir, ticker, item):
    cache = load_cache(base_dir)
    cache[ticker] = {
        **item,
        "schema_version": CACHE_SCHEMA_VERSION,
        "resolved_at": utc_ts(),
        "resolved_epoch": time.time(),
    }
    write_cache(base_dir, cache)


def existing_company(base_dir, ticker):
    active = load_active_universe(base_dir)
    for row in active.get("companies", []):
        if _clean_ticker(row.get("t")) == ticker:
            return dict(row)
    for file_name in ("watchlist.json", "company_overrides.json"):
        payload = read_json(Path(base_dir) / "data" / file_name, {})
        for row in payload.get("companies", []) if isinstance(payload, dict) else []:
            if _clean_ticker(row.get("t")) == ticker:
                out = dict(row)
                out.setdefault("source", "existing")
                return out
    return None


def city_lookup_key(city, country):
    return f"{str(city or '').strip().lower()}|{str(country or '').strip().lower()}"


def city_match(base_dir, city, country):
    cities = read_json(Path(base_dir) / "data" / "city_coords.json", {})
    key = city_lookup_key(city, country)
    if key in cities:
        return dict(cities[key])
    normalized_country = str(country or "").strip().lower()
    normalized_city = str(city or "").strip().lower()
    for item_key, row in cities.items():
        c, _, k_country = item_key.partition("|")
        if c == normalized_city and (not normalized_country or normalized_country in k_country or k_country in normalized_country):
            return dict(row)
    return None


def exchange_fallback(base_dir, ticker):
    exchanges = read_json(Path(base_dir) / "data" / "exchange_coords.json", {})
    exchange = exchange_of_ticker(ticker)
    row = dict(exchanges.get(exchange) or exchanges.get("US") or {})
    row["exchange"] = exchange
    row["location_source"] = "exchange"
    return row


def chart_url(ticker):
    encoded = urllib.parse.quote(ticker, safe="")
    return f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded}?range=7d&interval=1d"


def validate_quote(yf, ticker):
    req = urllib.request.Request(
        chart_url(ticker),
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json,text/plain,*/*",
        },
    )
    with urllib.request.urlopen(req, timeout=8) as response:
        payload = json.loads(response.read())

    chart = payload.get("chart") or {}
    if chart.get("error"):
        raise ValueError(str(chart["error"])[:180])
    results = chart.get("result") or []
    if not results:
        raise ValueError("Yahoo returned no recent price rows")

    quote = ((results[0].get("indicators") or {}).get("quote") or [{}])[0]
    closes = [value for value in quote.get("close", []) if value is not None]
    if not closes:
        raise ValueError("Yahoo returned no valid close price")
    return closes, (results[0].get("meta") or {})


def ticker_info(yf, ticker):
    try:
        return yf.Ticker(ticker).get_info() or {}
    except Exception:
        try:
            return yf.Ticker(ticker).info or {}
        except Exception:
            return {}


def resolved_name(ticker, info, override_name=None, chart_meta=None):
    if override_name:
        return override_name.strip()
    for key in ("longName", "shortName", "displayName", "symbol"):
        value = str(info.get(key) or "").strip()
        if value:
            return value
    # yfinance get_info() 被限流时的兜底：chart meta 同样带公司名。
    for key in ("longName", "shortName"):
        value = str((chart_meta or {}).get(key) or "").strip()
        if value:
            return value
    return ticker


def resolve_ticker(base_dir, ticker, override_name=None, force=False):
    ticker = normalize_ticker(ticker)
    existing = existing_company(base_dir, ticker)
    if existing and not force:
        if override_name:
            existing["name"] = override_name.strip()
        existing.setdefault("source", "existing")
        existing.setdefault("location_source", "existing")
        _as_company_tuple(existing)
        return existing

    cached = cached_result(base_dir, ticker)
    if cached and not force:
        if cached.get("status") == "ok":
            data = dict(cached["data"])
            if override_name:
                data["name"] = override_name.strip()
            _as_company_tuple(data)
            return data
        raise ValueError(cached.get("reason") or "cached ticker resolution failure")

    try:
        import yfinance as yf
        _, chart_meta = validate_quote(yf, ticker)
        info = ticker_info(yf, ticker)
        name = resolved_name(ticker, info, override_name, chart_meta)
        raw_city = info.get("city") or ""
        raw_country = info.get("country") or ""
        location = city_match(base_dir, raw_city, raw_country)
        location_source = "company_profile" if location else "exchange"
        if not location:
            location = exchange_fallback(base_dir, ticker)

        row = {
            "t": ticker,
            "name": name,
            "name_en": name,
            "country": location.get("country") or raw_country or "美国",
            "country_en": location.get("country_en") or raw_country or "United States",
            "city": location.get("city") or raw_city or "New York",
            "lat": float(location["lat"]),
            "lon": float(location["lon"]),
            "seg": "custom",
            "seg_en": "Custom watchlist",
            "chain": "自选监测",
            "chain_en": "Custom watchlist",
            "chain_key": "other",
            "source": "auto",
            "location_source": location_source,
            "exchange": exchange_of_ticker(ticker),
            "added_at": utc_ts(),
        }
        _as_company_tuple(row)
        remember(base_dir, ticker, {"status": "ok", "data": row})
        return row
    except Exception as exc:
        reason = str(exc)[:240]
        remember(base_dir, ticker, {"status": "failed", "reason": reason})
        raise ValueError(reason) from exc
