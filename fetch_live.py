# -*- coding: utf-8 -*-
"""实时快照：当前价 vs 前收 -> data/live.json（供网页实时模式轮询）"""
import datetime
import fcntl
import json
import os
import pathlib
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed

from aicm_io import data_lock, write_json_atomic

warnings.filterwarnings("ignore")

BASE = pathlib.Path(__file__).parent
DATA = BASE / "data"
LIVE_PATH = DATA / "live.json"
STATUS_PATH = DATA / "live_status.json"
LOCK_PATH = DATA / "live.lock"

STATUS_SCHEMA_VERSION = 2
LIVE_SOURCE = os.getenv("AICM_LIVE_SOURCE", "chart").strip().lower()
DATA_PROVIDER = os.getenv("AICM_DATA_PROVIDER", "yahoo_chart")
DATA_PROVIDER_LABEL = os.getenv("AICM_DATA_PROVIDER_LABEL", "Yahoo Finance chart polling")
DATA_DELAY_NOTE = os.getenv("AICM_DATA_DELAY_NOTE", "near-real-time where available; delayed by provider/exchange")


def env_int(name, default):
    try:
        return max(1, int(os.getenv(name, default)))
    except (TypeError, ValueError):
        return default


def env_float(name, default):
    try:
        return max(0.0, float(os.getenv(name, default)))
    except (TypeError, ValueError):
        return default


POLL_SECONDS = env_int("AICM_PAGE_POLL_SECONDS", 15)
REFRESH_SECONDS = env_int("AICM_REFRESH_SECONDS", 180)
LIVE_BATCH_SIZE = env_int("AICM_LIVE_BATCH_SIZE", 35)
LIVE_BATCH_PAUSE = env_float("AICM_LIVE_BATCH_PAUSE", 1.8)
CHART_TIMEOUT = env_float("AICM_CHART_TIMEOUT", 5)
CHART_PAUSE = env_float("AICM_CHART_PAUSE", 0.02)
CHART_WORKERS = env_int("AICM_CHART_WORKERS", 8)
CHART_RETRY_WORKERS = env_int("AICM_CHART_RETRY_WORKERS", 3)
CHART_RETRY_DELAY = env_float("AICM_CHART_RETRY_DELAY", 1.0)
RATE_LIMIT_BACKOFF_SECONDS = env_int("AICM_RATE_LIMIT_BACKOFF_SECONDS", 1800)


def provider_meta():
    return {
        "status_schema_version": STATUS_SCHEMA_VERSION,
        "data_provider": DATA_PROVIDER,
        "data_provider_label": DATA_PROVIDER_LABEL,
        "data_delay_note": DATA_DELAY_NOTE,
        "transport": "polling_json",
        "realtime_grade": "near_real_time",
        "strict_realtime": False,
        "live_source": LIVE_SOURCE,
    }


def ts():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def minute_ts():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M")


def load_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_json(path, payload):
    with data_lock(BASE):
        write_json_atomic(path, payload)


def write_status(**fields):
    status = load_json(STATUS_PATH)
    status.update(fields)
    status.update({
        "poll_seconds": POLL_SECONDS,
        "refresh_seconds": REFRESH_SECONDS,
    })
    status.update(provider_meta())
    write_json(STATUS_PATH, status)


def load_tickers():
    active = load_json(DATA / "active_universe.json")
    tickers = [c.get("t") for c in active.get("companies", []) if c.get("t")]
    if tickers:
        return tickers, active.get("counts", {})

    market_data = load_json(DATA / "semi_market.json")
    tickers = [c.get("t") for c in market_data.get("companies", []) if c.get("t")]
    if tickers:
        return tickers, {
            "active": len(tickers),
            "source": "semi_market",
        }

    # 兜底复用 fetch_data.py 里的公司表，只执行公司字典定义段。
    spec_src = (BASE / "fetch_data.py").read_text(encoding="utf-8")
    ns = {"__file__": str(BASE / "fetch_data.py")}
    marker = "\nwith data_lock(BASE):"
    if marker not in spec_src:
        marker = "\nrebuild_active_universe(BASE, C)"
    exec(spec_src.split(marker, 1)[0], ns)
    tickers = list(ns["C"].keys())
    return tickers, {
        "active": len(tickers),
        "source": "builtin",
    }


def load_market_fallbacks():
    market_data = load_json(DATA / "semi_market.json")
    items = {}
    for c in market_data.get("companies", []):
        try:
            t = c["t"]
            closes = c.get("close", [])
            vols = c.get("volusd", [])
            dates = c.get("dates", [])
            if len(closes) < 2:
                continue
            last = float(closes[-1])
            prev = float(closes[-2])
            items[t] = {
                "t": t,
                "p": round(last, 3),
                "chg": round((last / prev - 1) * 100, 2),
                "volusd": round(float(vols[-1]) if vols else 0, 1),
                "d": dates[-1][5:] if dates else "",
                "stale": True,
                "stale_source": "history",
            }
        except Exception:
            continue
    return items


def cur_of(t):
    if t.endswith((".TW", ".TWO")):
        return "TWD"
    if t.endswith((".KS", ".KQ")):
        return "KRW"
    if t.endswith(".T"):
        return "JPY"
    if t.endswith((".AS", ".DE", ".PA", ".BR", ".MC")):
        return "EUR"
    if t.endswith(".MI"):
        return "EUR"
    if t.endswith(".OL"):
        return "NOK"
    if t.endswith(".ST"):
        return "SEK"
    if t.endswith(".SW"):
        return "CHF"
    if t.endswith(".HK"):
        return "HKD"
    if t.endswith((".SS", ".SZ", ".BJ")):
        return "CNY"
    if t.endswith(".KL"):
        return "MYR"
    if t.endswith(".SI"):
        return "SGD"
    if t.endswith(".L"):
        return "GBP"
    if t.endswith(".SR"):
        return "SAR"
    if t.endswith(".NS"):
        return "INR"
    if t.endswith(".AX"):
        return "AUD"
    if t.endswith(".V"):
        return "CAD"
    if t.endswith(".CL"):
        return "COP"
    if t.endswith(".JO"):
        return "ZAR"
    return "USD"


def price_scale_of(t):
    return 0.01 if t.endswith(".JO") else 1.0


def fetch_fx(yf):
    fx = {"USD": 1.0}
    fx_map = {
        "TWD": "TWD=X",
        "KRW": "KRW=X",
        "JPY": "JPY=X",
        "EUR": "EURUSD=X",
        "CNY": "CNY=X",
        "HKD": "HKD=X",
        "CHF": "CHF=X",
        "NOK": "NOK=X",
        "SEK": "SEK=X",
        "SGD": "SGD=X",
        "MYR": "MYR=X",
        "ILS": "ILS=X",
        "GBP": "GBPUSD=X",
        "SAR": "SAR=X",
        "INR": "INR=X",
        "AUD": "AUDUSD=X",
        "CAD": "CAD=X",
        "COP": "COP=X",
        "ZAR": "ZAR=X",
    }
    for cur, sym in fx_map.items():
        try:
            close = yf.Ticker(sym).history(period="5d")["Close"]
            value = float(close.iloc[-1])
            fx[cur] = value if cur in ("EUR", "GBP", "AUD") else 1.0 / value
        except Exception:
            fx[cur] = 0
    return fx


def _last_number(values):
    for value in reversed(values or []):
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _first_number(*values):
    for value in values:
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _chart_url(symbol):
    encoded = urllib.parse.quote(symbol, safe="")
    return f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded}?range=7d&interval=1d"


def fetch_chart_quote(ticker):
    req = urllib.request.Request(
        _chart_url(ticker),
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json,text/plain,*/*",
        },
    )
    with urllib.request.urlopen(req, timeout=CHART_TIMEOUT) as response:
        payload = json.loads(response.read())

    chart = payload.get("chart") or {}
    if chart.get("error"):
        raise ValueError(str(chart["error"])[:180])
    results = chart.get("result") or []
    if not results:
        raise ValueError("empty chart result")

    result = results[0]
    meta = result.get("meta") or {}
    quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    closes = [value for value in quote.get("close", []) if value is not None]
    volumes = [value for value in quote.get("volume", []) if value is not None]
    if not closes:
        raise ValueError("chart has no close prices")

    last_raw = _first_number(meta.get("regularMarketPrice"), _last_number(closes))
    prev_raw = float(closes[-2]) if len(closes) >= 2 else None
    if prev_raw is None:
        prev_raw = _first_number(meta.get("previousClose"), meta.get("chartPreviousClose"))
    if last_raw is None or prev_raw in (None, 0):
        raise ValueError("chart has insufficient price history")

    scale = price_scale_of(ticker)
    regular_time = meta.get("regularMarketTime") or _last_number(result.get("timestamp", []))
    date_text = ""
    if regular_time:
        date_text = datetime.datetime.utcfromtimestamp(int(regular_time)).strftime("%m-%d")

    return {
        "p": round(last_raw * scale, 3),
        "chg": round((last_raw / prev_raw - 1) * 100, 2),
        "vol": float(_first_number(meta.get("regularMarketVolume"), _last_number(volumes), 0) or 0),
        "d": date_text,
        "market_time": int(regular_time) if regular_time else None,
        "source": "yahoo_chart",
    }


def fetch_chart_quotes(tickers):
    if not tickers:
        return {}, []

    def run_batch(batch, workers):
        quotes = {}
        failures = {}
        with ThreadPoolExecutor(max_workers=max(1, min(workers, len(batch)))) as executor:
            futures = {}
            for index, ticker in enumerate(batch):
                futures[executor.submit(fetch_chart_quote, ticker)] = ticker
                if CHART_PAUSE and index < len(batch) - 1:
                    time.sleep(CHART_PAUSE)

            for future in as_completed(futures):
                ticker = futures[future]
                try:
                    quotes[ticker] = future.result()
                except Exception as exc:
                    failures[ticker] = str(exc)[:140]
        return quotes, failures

    quotes, failures = run_batch(tickers, CHART_WORKERS)
    retry_tickers = [ticker for ticker in tickers if ticker in failures]
    if retry_tickers:
        time.sleep(CHART_RETRY_DELAY)
        retry_quotes, retry_failures = run_batch(retry_tickers, CHART_RETRY_WORKERS)
        quotes.update(retry_quotes)
        failures = {ticker: msg for ticker, msg in failures.items() if ticker not in retry_quotes}
        failures.update(retry_failures)

    failed = [f"{ticker}: chart failed: {message}" for ticker, message in failures.items()]
    return quotes, failed


def fetch_fx_chart():
    fx = {"USD": 1.0}
    fx_map = {
        "TWD": "TWD=X",
        "KRW": "KRW=X",
        "JPY": "JPY=X",
        "EUR": "EURUSD=X",
        "CNY": "CNY=X",
        "HKD": "HKD=X",
        "CHF": "CHF=X",
        "NOK": "NOK=X",
        "SEK": "SEK=X",
        "SGD": "SGD=X",
        "MYR": "MYR=X",
        "ILS": "ILS=X",
        "GBP": "GBPUSD=X",
        "SAR": "SAR=X",
        "INR": "INR=X",
        "AUD": "AUDUSD=X",
        "CAD": "CAD=X",
        "COP": "COP=X",
        "ZAR": "ZAR=X",
    }
    for cur, sym in fx_map.items():
        try:
            value = fetch_chart_quote(sym)["p"]
            fx[cur] = value if cur in ("EUR", "GBP", "AUD") else 1.0 / value
        except Exception:
            fx[cur] = 0
    return fx


def chunks(values, size):
    for i in range(0, len(values), size):
        yield values[i:i + size]


def frame_for_ticker(px, ticker, chunk_len):
    if px is None or getattr(px, "empty", True):
        return None
    cols = getattr(px, "columns", None)
    if chunk_len == 1:
        return px
    if cols is not None and hasattr(cols, "get_level_values"):
        try:
            if ticker in set(cols.get_level_values(0)):
                return px[ticker]
        except Exception:
            return None
    return None


def download_frames(yf, tickers):
    frames = {}
    failed = []
    for batch_no, batch in enumerate(chunks(tickers, LIVE_BATCH_SIZE), start=1):
        try:
            px = yf.download(
                batch,
                period="7d",
                interval="1d",
                group_by="ticker",
                auto_adjust=True,
                threads=min(4, max(1, len(batch))),
                progress=False,
            )
        except Exception as exc:
            failed.extend(f"{t}: batch {batch_no} download failed: {str(exc)[:120]}" for t in batch)
            time.sleep(LIVE_BATCH_PAUSE)
            continue
        for ticker in batch:
            frames[ticker] = frame_for_ticker(px, ticker, len(batch))
        if batch_no * LIVE_BATCH_SIZE < len(tickers):
            time.sleep(LIVE_BATCH_PAUSE)
    return frames, failed


def _frame_is_usable(df):
    if df is None:
        return False
    try:
        return len(df.dropna(subset=["Close"])) >= 2
    except Exception:
        return False


def main():
    DATA.mkdir(exist_ok=True)
    with LOCK_PATH.open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            write_status(
                status="busy",
                last_attempt=ts(),
                message="上一轮实时行情仍在运行，本轮跳过",
            )
            print("live fetch skipped: previous run still active")
            return 0

        previous_status = load_json(STATUS_PATH)
        backoff_until = float(previous_status.get("backoff_until_epoch") or 0)
        backoff_source = previous_status.get("backoff_source") or "yfinance"
        if backoff_until and time.time() < backoff_until and backoff_source in ("all", LIVE_SOURCE):
            wait_s = int(backoff_until - time.time())
            write_status(
                status="backoff",
                last_attempt=ts(),
                message=f"行情源限流保护中，约 {wait_s} 秒后再试",
                backoff_until_epoch=backoff_until,
                backoff_source=backoff_source,
            )
            print(f"live fetch skipped: provider backoff {wait_s}s")
            return 0

        started_at = ts()
        started = time.time()
        failed = []
        write_status(
            status="running",
            started_at=started_at,
            last_attempt=started_at,
            message="正在更新实时行情",
        )

        try:
            tickers, universe_counts = load_tickers()
            previous_items = load_json(LIVE_PATH).get("items", {})
            market_fallbacks = load_market_fallbacks()
            frames = {}
            download_failed = []
            chart_quotes = {}
            chart_failed = []
            source_used = "yahoo_chart"

            if LIVE_SOURCE in ("yfinance", "auto"):
                import yfinance as yf

                frames, download_failed = download_frames(yf, tickers)
                source_used = "yfinance"

            missing_for_chart = [t for t in tickers if not _frame_is_usable(frames.get(t))]
            if LIVE_SOURCE in ("chart", "yahoo_chart", "auto") and missing_for_chart:
                chart_quotes, chart_failed = fetch_chart_quotes(missing_for_chart)
                if chart_quotes:
                    source_used = "mixed" if source_used == "yfinance" and len(chart_quotes) < len(tickers) else "yahoo_chart"

            if LIVE_SOURCE in ("yfinance", "auto") and not chart_quotes:
                fx = fetch_fx(yf)
            else:
                fx = fetch_fx_chart()

            out = {
                "asof": minute_ts(),
                "items": {},
                "meta": {
                    "status": "success",
                    "poll_seconds": POLL_SECONDS,
                    "refresh_seconds": REFRESH_SECONDS,
                    "active_universe_count": universe_counts.get("active", len(tickers)),
                    "user_watchlist_count": universe_counts.get("user_add", 0),
                    "data_source_used": source_used,
                    **provider_meta(),
                },
            }
            provider_warnings = list(download_failed)
            failed = []
            stale_count = 0
            fresh_count = 0
            for t in tickers:
                try:
                    df = frames.get(t)
                    if _frame_is_usable(df):
                        df = df.dropna(subset=["Close"])
                        scale = price_scale_of(t)
                        last = float(df["Close"].iloc[-1]) * scale
                        prev = float(df["Close"].iloc[-2]) * scale
                        vol = float(df["Volume"].fillna(0).iloc[-1])
                        rate = fx.get(cur_of(t)) or 0
                        out["items"][t] = {
                            "t": t,
                            "p": round(last, 3),
                            "chg": round((last / prev - 1) * 100, 2),
                            "volusd": round(last * vol * rate / 1e6, 1),
                            "d": df.index[-1].strftime("%m-%d"),
                            "market_time": df.index[-1].isoformat(),
                            "source": "yfinance",
                        }
                        fresh_count += 1
                        continue

                    quote = chart_quotes.get(t)
                    if quote:
                        rate = fx.get(cur_of(t)) or 0
                        out["items"][t] = {
                            "t": t,
                            "p": quote["p"],
                            "chg": quote["chg"],
                            "volusd": round(quote["p"] * quote.get("vol", 0) * rate / 1e6, 1),
                            "d": quote.get("d", ""),
                            "market_time": quote.get("market_time"),
                            "source": quote.get("source", "yahoo_chart"),
                        }
                        fresh_count += 1
                        continue

                    raise ValueError("no downloaded frame or chart quote")
                except Exception as exc:
                    failed.append(f"{t}: {exc}")
                    fallback = previous_items.get(t) or market_fallbacks.get(t)
                    if fallback:
                        out["items"][t] = {**fallback, "t": t, "stale": True, "stale_reason": str(exc)[:120]}
                        stale_count += 1

            failed.extend(chart_failed)
            no_frame_count = sum("no downloaded frame" in item for item in failed)
            yfinance_limited = any("Too Many Requests" in item or "Rate limited" in item for item in provider_warnings)
            rate_limited = (
                yfinance_limited
                or any("Too Many Requests" in item or "Rate limited" in item for item in failed)
                or no_frame_count > len(tickers) * 0.5
            )
            if len(out["items"]) < max(1, int(len(tickers) * 0.7)):
                suffix = "; provider rate limited" if rate_limited else ""
                raise RuntimeError(f"too few live items: {len(out['items'])}/{len(tickers)}{suffix}")

            finished_at = ts()
            elapsed_s = round(time.time() - started, 2)
            out["meta"].update({
                "started_at": started_at,
                "finished_at": finished_at,
                "elapsed_s": elapsed_s,
                "item_count": len(out["items"]),
                "expected_count": len(tickers),
                "active_universe_count": universe_counts.get("active", len(tickers)),
                "user_watchlist_count": universe_counts.get("user_add", 0),
                "fresh_count": fresh_count,
                "stale_count": stale_count,
                "failed_count": len(failed),
                "failed_sample": failed[:8],
                "warning_count": len(provider_warnings),
                "warning_sample": provider_warnings[:8],
                "data_source_used": source_used,
            })
            write_json(LIVE_PATH, out)
            write_status(
                status="success",
                started_at=started_at,
                last_attempt=started_at,
                finished_at=finished_at,
                last_success=finished_at,
                elapsed_s=elapsed_s,
                item_count=len(out["items"]),
                expected_count=len(tickers),
                active_universe_count=universe_counts.get("active", len(tickers)),
                user_watchlist_count=universe_counts.get("user_add", 0),
                fresh_count=fresh_count,
                stale_count=stale_count,
                failed_count=len(failed),
                failed_sample=failed[:8],
                warning_count=len(provider_warnings),
                warning_sample=provider_warnings[:8],
                data_source_used=source_used,
                backoff_until_epoch=0,
                backoff_source="",
                rate_limited=False,
                last_error="",
                message="实时行情更新成功",
            )
            print(f"live.json: {len(out['items'])} tickers @ {out['asof']} ({elapsed_s}s)")
            return 0
        except Exception as exc:
            finished_at = ts()
            elapsed_s = round(time.time() - started, 2)
            no_frame_count = sum("no downloaded frame" in item for item in failed)
            rate_limited = (
                "rate limited" in str(exc).lower()
                or "provider rate limited" in str(exc).lower()
                or any("Too Many Requests" in item or "Rate limited" in item for item in failed)
                or no_frame_count > 0
            )
            backoff = time.time() + RATE_LIMIT_BACKOFF_SECONDS if rate_limited else 0
            backoff_source = "all" if LIVE_SOURCE in ("chart", "yahoo_chart", "auto") else "yfinance"
            write_status(
                status="error",
                started_at=started_at,
                finished_at=finished_at,
                elapsed_s=elapsed_s,
                last_error=str(exc),
                message="实时行情更新失败，保留上一份 live.json",
                backoff_until_epoch=backoff,
                backoff_source=backoff_source if backoff else "",
                rate_limited=rate_limited,
            )
            print(f"live fetch failed: {exc}", file=sys.stderr)
            traceback.print_exc()
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
