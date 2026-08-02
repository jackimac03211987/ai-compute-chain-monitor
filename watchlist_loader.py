# -*- coding: utf-8 -*-
"""Load listed-company metadata for the AI compute dashboard.

The active monitor is intentionally data-driven:

- data/watchlist.json is the reviewed production watchlist once matrices arrive.
- data/company_overrides.json can add or correct ticker metadata without editing
  Python source.
- If neither file exists, the system falls back to the built-in company list.
"""

import json
from pathlib import Path

from aicm_io import read_json, utc_ts, write_json_atomic


COMPANY_FIELDS = ("name", "country", "city", "lat", "lon", "seg")
USER_WATCHLIST_NAME = "user_watchlist.json"
ACTIVE_UNIVERSE_NAME = "active_universe.json"


def _clean_ticker(value):
    ticker = str(value or "").strip().upper()
    if not ticker:
        raise ValueError("missing ticker")
    return ticker


def _row_from_tuple(values):
    name, country, city, lat, lon, seg = values
    return {
        "name": name,
        "country": country,
        "city": city,
        "lat": lat,
        "lon": lon,
        "seg": seg,
    }


def _row_from_company_tuple(ticker, values, source="base"):
    row = _row_from_tuple(values)
    row["t"] = _clean_ticker(ticker)
    row.setdefault("source", source)
    return row


def _merge_company_row(ticker, base_row, override_row):
    merged = dict(base_row or {})
    for field in COMPANY_FIELDS:
        source_field = "segment" if field == "seg" and "seg" not in override_row else field
        if source_field in override_row and str(override_row[source_field]).strip() != "":
            merged[field] = override_row[source_field]

    missing = [field for field in COMPANY_FIELDS if str(merged.get(field, "")).strip() == ""]
    if missing:
        raise ValueError(f"{ticker}: missing required metadata fields: {', '.join(missing)}")
    return merged


def _as_company_tuple(row):
    lat = float(row["lat"])
    lon = float(row["lon"])
    if not -90 <= lat <= 90:
        raise ValueError(f"lat out of range: {lat}")
    if not -180 <= lon <= 180:
        raise ValueError(f"lon out of range: {lon}")
    return (
        row["name"],
        row["country"],
        row.get("city", ""),
        lat,
        lon,
        row.get("seg", row.get("segment", "其他算力产业链")),
    )


def _normalize_company_row(ticker, row, source="user"):
    merged = _merge_company_row(ticker, {}, row)
    normalized = {
        "t": _clean_ticker(ticker),
        **merged,
        "source": row.get("source", source),
    }
    for optional in (
        "name_zh",
        "name_en",
        "country_en",
        "region",
        "region_en",
        "seg_en",
        "chain",
        "chain_en",
        "chain_key",
        "location_source",
        "added_at",
    ):
        if optional in row and row[optional] not in ("", None):
            normalized[optional] = row[optional]
    # Validate numeric bounds through the existing tuple converter.
    _as_company_tuple(normalized)
    return normalized


def load_company_overrides(base_dir):
    """Return ticker -> metadata row from data/company_overrides.json."""
    path = Path(base_dir) / "data" / "company_overrides.json"
    if not path.exists():
        return {}

    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        rows = payload
    else:
        rows = payload.get("companies") or payload.get("overrides") or []

    overrides = {}
    for row in rows:
        try:
            ticker = _clean_ticker(row["t"])
            overrides[ticker] = dict(row)
        except Exception as exc:
            raise ValueError(f"Invalid company override row: {row!r}") from exc
    return overrides


def apply_company_overrides(base_dir, companies, allow_new=True):
    """Apply data/company_overrides.json to a ticker -> company tuple mapping."""
    overrides = load_company_overrides(base_dir)
    if not overrides:
        return companies

    merged = dict(companies)
    for ticker, override_row in overrides.items():
        if ticker not in merged and not allow_new:
            continue
        base_row = _row_from_tuple(merged[ticker]) if ticker in merged else {}
        try:
            merged_row = _merge_company_row(ticker, base_row, override_row)
            merged[ticker] = _as_company_tuple(merged_row)
        except Exception as exc:
            raise ValueError(f"Invalid metadata for {ticker} in company_overrides.json") from exc
    return merged


def load_watchlist(base_dir, default_companies):
    """Return ticker -> company tuple.

    `data/watchlist.json` is the reviewed source of truth once the user's two
    industry-chain matrices are imported. Until that file exists, the system
    safely falls back to the current built-in monitor list.
    """
    base = Path(base_dir)
    watchlist_path = base / "data" / "watchlist.json"
    if not watchlist_path.exists():
        return apply_company_overrides(base, default_companies, allow_new=True)

    payload = json.loads(watchlist_path.read_text(encoding="utf-8"))
    rows = payload.get("companies", [])
    loaded = {}
    for row in rows:
        try:
            ticker = row["t"].strip()
            loaded[ticker] = _as_company_tuple(row)
        except Exception as exc:
            raise ValueError(f"Invalid watchlist row: {row!r}") from exc

    if not loaded:
        raise ValueError(f"{watchlist_path} has no valid companies")
    return apply_company_overrides(base, loaded, allow_new=False)


def default_user_watchlist():
    return {"version": 1, "replace_base": False, "add": {}, "remove": []}


def load_user_watchlist(base_dir):
    path = Path(base_dir) / "data" / USER_WATCHLIST_NAME
    payload = read_json(path, default_user_watchlist())
    if not isinstance(payload, dict):
        payload = default_user_watchlist()
    payload.setdefault("version", 1)
    payload.setdefault("replace_base", False)
    payload.setdefault("add", {})
    payload.setdefault("remove", [])
    if not isinstance(payload["add"], dict):
        payload["add"] = {}
    if not isinstance(payload["remove"], list):
        payload["remove"] = []
    return payload


def write_user_watchlist(base_dir, payload):
    normalized = default_user_watchlist()
    normalized.update(payload or {})
    normalized["replace_base"] = bool(normalized.get("replace_base"))
    add = {}
    for ticker, row in (normalized.get("add") or {}).items():
        t = _clean_ticker(ticker)
        row = dict(row or {})
        row["t"] = t
        add[t] = row
    normalized["add"] = add
    normalized["remove"] = sorted({_clean_ticker(t) for t in normalized.get("remove", []) if str(t).strip()})
    write_json_atomic(Path(base_dir) / "data" / USER_WATCHLIST_NAME, normalized)
    return normalized


def build_active_universe(base_dir, default_companies):
    base = Path(base_dir)
    user = load_user_watchlist(base)
    replace_base = bool(user.get("replace_base"))
    removed = {_clean_ticker(t) for t in user.get("remove", []) if str(t).strip()}

    rows = {}
    base_count = 0
    if not replace_base:
        base_companies = load_watchlist(base, default_companies)
        base_count = len(base_companies)
        for ticker, values in base_companies.items():
            t = _clean_ticker(ticker)
            if t in removed:
                continue
            rows[t] = _row_from_company_tuple(t, values, source="base")

    user_added = 0
    for ticker, row in (user.get("add") or {}).items():
        t = _clean_ticker(ticker)
        row = dict(row or {})
        normalized = _normalize_company_row(t, row, source=row.get("source", "user"))
        normalized["source"] = normalized.get("source") or "user"
        rows[t] = normalized
        user_added += 1

    companies = [rows[t] for t in sorted(rows)]
    return {
        "version": 1,
        "generated_at": utc_ts(),
        "replace_base": replace_base,
        "companies": companies,
        "counts": {
            "base": base_count,
            "user_add": user_added,
            "removed": len(removed),
            "active": len(companies),
        },
    }


def rebuild_active_universe(base_dir, default_companies):
    payload = build_active_universe(base_dir, default_companies)
    write_json_atomic(Path(base_dir) / "data" / ACTIVE_UNIVERSE_NAME, payload)
    return payload


def load_active_universe(base_dir):
    path = Path(base_dir) / "data" / ACTIVE_UNIVERSE_NAME
    payload = read_json(path, {})
    if not isinstance(payload, dict):
        return {}
    companies = payload.get("companies")
    if not isinstance(companies, list):
        return {}
    return payload


def active_universe_as_tuples(base_dir, default_companies, rebuild_if_missing=True):
    payload = load_active_universe(base_dir)
    if not payload and rebuild_if_missing:
        payload = rebuild_active_universe(base_dir, default_companies)
    companies = {}
    for row in payload.get("companies", []):
        ticker = _clean_ticker(row.get("t"))
        companies[ticker] = _as_company_tuple(row)
    return companies
