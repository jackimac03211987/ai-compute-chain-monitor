# -*- coding: utf-8 -*-
"""Audit listed-company watchlists before they drive the dashboard."""

import argparse
import csv
import datetime as dt
import json
from collections import Counter, defaultdict
from pathlib import Path

from industry_chain import enrich_company


DATA_DIR = Path(__file__).parent / "data"
FOCUS_COUNTRIES = ("美国", "中国大陆", "中国台湾")


def exchange_of(ticker):
    suffix_map = {
        ".SS": "SSE",
        ".SZ": "SZSE",
        ".BJ": "BSE Beijing",
        ".HK": "HKEX",
        ".TW": "TWSE",
        ".TWO": "TPEX",
        ".KS": "KRX",
        ".KQ": "KOSDAQ",
        ".T": "TSE",
        ".AS": "Euronext Amsterdam",
        ".DE": "Xetra",
        ".PA": "Euronext Paris",
        ".BR": "Euronext Brussels",
        ".MC": "BME",
        ".MI": "Borsa Italiana",
        ".OL": "Oslo Bors",
        ".SW": "SIX",
        ".ST": "Nasdaq Stockholm",
        ".KL": "Bursa Malaysia",
        ".SI": "SGX",
        ".L": "LSE",
        ".SR": "Tadawul",
        ".NS": "NSE India",
        ".AX": "ASX",
        ".V": "TSX Venture",
        ".CL": "BVC",
        ".JO": "JSE",
    }
    for suffix, exchange in suffix_map.items():
        if ticker.endswith(suffix):
            return exchange
    return "US"


def status_of(row):
    return str(row.get("status", "recognized")).strip() or "recognized"


def clean_sources(value):
    if isinstance(value, list):
        return [str(x) for x in value if str(x).strip()]
    return [x for x in str(value or "").split(";") if x]


def parse_float(value):
    try:
        return float(str(value).strip())
    except Exception:
        return None


def chain_meta(row):
    if status_of(row) in {"needs_review", "excluded"}:
        return None
    required = ("t", "name", "country", "city", "seg")
    if any(not str(row.get(field, "")).strip() for field in required):
        return None
    return enrich_company(
        str(row["t"]).strip(),
        str(row["name"]).strip(),
        str(row["country"]).strip(),
        str(row["city"]).strip(),
        str(row["seg"]).strip(),
    )


def audit_rows(rows):
    excluded = [r for r in rows if status_of(r) == "excluded"]
    recognized = [r for r in rows if status_of(r) not in {"needs_review", "excluded"}]
    needs_review = [r for r in rows if status_of(r) == "needs_review"]
    countries = Counter()
    regions = Counter()
    chains = Counter()
    exchanges = Counter()
    sources = Counter()
    focus = {country: {"companies": 0, "chains": Counter()} for country in FOCUS_COUNTRIES}
    missing_fields = []
    coordinate_issues = []
    coord_buckets = defaultdict(list)

    for row in rows:
        for source in clean_sources(row.get("matrix_sources", "")):
            sources[source] += 1

        ticker = str(row.get("t", "")).strip()
        if not ticker:
            continue
        exchanges[exchange_of(ticker)] += 1

        if status_of(row) in {"needs_review", "excluded"}:
            continue

        missing = [
            field for field in ("name", "country", "city", "lat", "lon", "seg")
            if not str(row.get(field, "")).strip()
        ]
        if missing:
            missing_fields.append({"t": ticker, "missing": missing})
            continue

        lat = parse_float(row.get("lat"))
        lon = parse_float(row.get("lon"))
        if lat is None or lon is None or not -90 <= lat <= 90 or not -180 <= lon <= 180:
            coordinate_issues.append({
                "t": ticker,
                "issue": "invalid_lat_lon",
                "lat": row.get("lat"),
                "lon": row.get("lon"),
            })
        else:
            coord_buckets[(round(lat, 3), round(lon, 3))].append(ticker)
            if abs(lat) < 0.001 and abs(lon) < 0.001:
                coordinate_issues.append({"t": ticker, "issue": "zero_coordinate", "lat": lat, "lon": lon})

        country = str(row.get("country", "")).strip()
        countries[country] += 1
        meta = chain_meta(row)
        if meta:
            regions[meta["region"]] += 1
            chains[meta["chain"]] += 1
            if country in focus:
                focus[country]["companies"] += 1
                focus[country]["chains"][meta["chain"]] += 1

    duplicate_coordinates = [
        {"lat": lat, "lon": lon, "tickers": tickers}
        for (lat, lon), tickers in sorted(coord_buckets.items())
        if len(tickers) >= 4
    ]

    return {
        "generated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "totals": {
            "rows": len(rows),
            "recognized": len(recognized),
            "needs_review": len(needs_review),
            "excluded": len(excluded),
            "missing_field_rows": len(missing_fields),
            "coordinate_issue_rows": len(coordinate_issues),
            "duplicate_coordinate_clusters": len(duplicate_coordinates),
        },
        "coverage": {
            "countries": dict(countries.most_common()),
            "regions": dict(regions.most_common()),
            "chains": dict(chains.most_common()),
            "exchanges": dict(exchanges.most_common()),
            "matrix_sources": dict(sources.most_common()),
            "focus": {
                country: {
                    "companies": data["companies"],
                    "chains": dict(data["chains"].most_common()),
                }
                for country, data in focus.items()
            },
        },
        "quality": {
            "needs_review_tickers": [str(r.get("t", "")).strip() for r in needs_review if str(r.get("t", "")).strip()],
            "excluded": [
                {
                    "name_or_ticker": str(r.get("name") or r.get("t") or "").strip(),
                    "reason": str(r.get("exclude_reason") or r.get("review_decision") or "").strip(),
                }
                for r in excluded[:100]
            ],
            "missing_fields": missing_fields[:100],
            "coordinate_issues": coordinate_issues[:100],
            "duplicate_coordinates": duplicate_coordinates[:100],
        },
    }


def load_candidate_csv(path):
    with Path(path).open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def load_watchlist_json(path):
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = payload.get("companies", [])
    return [{**row, "status": row.get("status", "recognized")} for row in rows]


def write_audit(path, audit):
    path = Path(path)
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", default=str(DATA_DIR / "watchlist_candidates.csv"))
    ap.add_argument("--watchlist", help="Audit an existing watchlist.json instead of a candidate CSV")
    ap.add_argument("--out", help="Output JSON path")
    args = ap.parse_args()

    if args.watchlist:
        rows = load_watchlist_json(args.watchlist)
        out = args.out or str(DATA_DIR / "watchlist_audit.json")
    else:
        rows = load_candidate_csv(args.candidates)
        out = args.out or str(DATA_DIR / "watchlist_candidates_audit.json")

    audit = audit_rows(rows)
    path = write_audit(out, audit)
    print(f"audit: {path}")
    print(json.dumps(audit["totals"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
