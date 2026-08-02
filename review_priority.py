# -*- coding: utf-8 -*-
"""Build a priority queue for unresolved matrix watchlist candidates.

This script is intentionally read-only with respect to production watchlists:
it reads data/watchlist_candidates.csv and writes review helper files only.
"""

import argparse
import csv
import json
import re
from pathlib import Path


BASE = Path(__file__).parent
DATA_DIR = BASE / "data"


VALUE_RE = re.compile(r"\|\s*上市[^|]*\|\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*([TBM])", re.I)


def parse_value_billion(context):
    values = []
    for raw, unit in VALUE_RE.findall(context or ""):
        value = float(raw.replace(",", ""))
        unit = unit.upper()
        if unit == "T":
            value *= 1000
        elif unit == "M":
            value /= 1000
        values.append(value)
    return max(values) if values else None


def source_count(value):
    return len([x for x in str(value or "").split(";") if x])


def candidate_name(row):
    return (row.get("name") or row.get("t") or "").strip()


def load_candidates(path):
    with Path(path).open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def build_priority(rows):
    unresolved = [row for row in rows if row.get("status") == "needs_review"]
    out = []
    for row in unresolved:
        context = row.get("matrix_context", "")
        value_b = parse_value_billion(context)
        name = candidate_name(row)
        candidate_type = "ticker" if row.get("t") else "name"
        out.append({
            "rank": 0,
            "name_or_ticker": name,
            "candidate_type": candidate_type,
            "value_usd_b": round(value_b, 3) if value_b is not None else "",
            "source_count": source_count(row.get("matrix_sources")),
            "matrix_sources": row.get("matrix_sources", ""),
            "review_action": "fill ticker/name/country/city/lat/lon/seg, then mark reviewed or approved",
            "matrix_context": context,
        })

    out.sort(key=lambda r: (
        r["value_usd_b"] if isinstance(r["value_usd_b"], (int, float)) else -1,
        r["source_count"],
        r["name_or_ticker"],
    ), reverse=True)
    for i, row in enumerate(out, start=1):
        row["rank"] = i
    return out


def write_outputs(rows, out_csv, out_json):
    out_csv = Path(out_csv)
    out_json = Path(out_json)
    out_csv.parent.mkdir(exist_ok=True)
    fields = [
        "rank",
        "name_or_ticker",
        "candidate_type",
        "value_usd_b",
        "source_count",
        "matrix_sources",
        "review_action",
        "matrix_context",
    ]
    with out_csv.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    summary = {
        "total_needs_review": len(rows),
        "with_value": sum(1 for row in rows if row["value_usd_b"] != ""),
        "top_50": rows[:50],
    }
    out_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", default=str(DATA_DIR / "watchlist_candidates.csv"))
    ap.add_argument("--out-csv", default=str(DATA_DIR / "watchlist_review_priority.csv"))
    ap.add_argument("--out-json", default=str(DATA_DIR / "watchlist_review_priority.json"))
    args = ap.parse_args()

    rows = load_candidates(args.candidates)
    priority = build_priority(rows)
    summary = write_outputs(priority, args.out_csv, args.out_json)
    print(json.dumps({
        "total_needs_review": summary["total_needs_review"],
        "with_value": summary["with_value"],
        "top_10": [
            {
                "rank": row["rank"],
                "name_or_ticker": row["name_or_ticker"],
                "value_usd_b": row["value_usd_b"],
            }
            for row in summary["top_50"][:10]
        ],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
