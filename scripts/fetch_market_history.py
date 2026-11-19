#!/usr/bin/env python3
"""Fetch raw and corporate-action-adjusted daily history for all screened shares/funds."""
from __future__ import annotations
import csv, gzip, hashlib, json, math, os, sys, time, urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

BASE = "https://cdn.tsetmc.com/api"
DATA = Path("data")
SCREENER = DATA / "market_screener.csv"
OUT = DATA / "market_history"
SHARDS = 16
WORKERS = 4
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.tsetmc.com/",
}
FIELDS = ["symbol", "name", "asset_type", "insCode", "date", "open", "high", "low", "close", "last", "volume", "value"]

def get_json(url, attempts=3):
    last = None
    for n in range(attempts):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=25) as response:
                return json.loads(response.read().decode("utf-8-sig"))
        except Exception as exc:
            last = exc
            if n + 1 < attempts:
                time.sleep(1.5 * (n + 1))
    raise RuntimeError(str(last))

def rows_from(payload, preferred):
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in preferred:
            if isinstance(payload.get(key), list):
                return [x for x in payload[key] if isinstance(x, dict)]
        for value in payload.values():
            if isinstance(value, list) and (not value or isinstance(value[0], dict)):
                return value
    raise ValueError("Unexpected TSETMC response shape")

def number(value):
    try:
        x = float(value or 0)
        return x if math.isfinite(x) else 0.0
    except (TypeError, ValueError):
        return 0.0

def event_date(value):
    try:
        d = int(number(value))
        datetime.strptime(str(d), "%Y%m%d")
        return d
    except Exception:
        return 0

def within_seven_days(a, b):
    da = datetime.strptime(str(a), "%Y%m%d").date()
    db = datetime.strptime(str(b), "%Y%m%d").date()
    return abs((da - db).days) <= 7

def fetch_one(item):
    code = str(item["insCode"]).strip()
    symbol = str(item.get("symbol", ""))
    name = str(item.get("name", ""))
    base = BASE
    daily_url = base + "/ClosingPrice/GetClosingPriceDailyList/" + code + "/0"
    adjust_url = base + "/ClosingPrice/GetPriceAdjustList/" + code
    shares_url = base + "/Instrument/GetInstrumentShareChange/" + code
    daily = rows_from(get_json(daily_url), ("closingPriceDaily",))
    if len(daily) < 20:
        raise RuntimeError("daily history too short: " + str(len(daily)))
    # Both event endpoints are required before a chart is labelled adjusted.
    adjustments = rows_from(get_json(adjust_url), ("priceAdjust",))
    share_changes = rows_from(get_json(shares_url), ("instrumentShareChange",))
    unique_shares = {}
    for event in share_changes:
        d = event_date(event.get("dEven"))
        old = number(event.get("numberOfShareOld"))
        new = number(event.get("numberOfShareNew"))
        if d and old >= 1000 and new >= 1000 and old != new:
            unique_shares[(d, old, new)] = {"date": d, "ratio": old / new, "source": "share_change"}
    events, adjust_dates = [], []
    for event in adjustments:
        d = event_date(event.get("dEven"))
        before = number(event.get("pClosingNotAdjusted"))
        after = number(event.get("pClosing"))
        if d and before > 0 and after > 0:
            events.append({"date": d, "ratio": after / before, "source": "price_adjust"})
            adjust_dates.append(d)
    for event in unique_shares.values():
        if not any(within_seven_days(event["date"], d) for d in adjust_dates):
            events.append(event)
    events.sort(key=lambda x: x["date"])
    clean, seen = [], set()
    for row in daily:
        d = event_date(row.get("dEven") or row.get("date"))
        if not d or d in seen:
            continue
        seen.add(d)
        values = {
            "symbol": symbol, "name": name, "asset_type": item.get("asset_type", ""),
            "insCode": code, "date": str(d),
            "open": number(row.get("priceFirst")),
            "high": number(row.get("priceMax")),
            "low": number(row.get("priceMin")),
            "close": number(row.get("pClosing")),
            "last": number(row.get("pDrCotVal")),
            "volume": int(number(row.get("qTotTran5J"))),
            "value": int(number(row.get("qTotCap"))),
        }
        if values["close"] > 0:
            clean.append(values)
    clean.sort(key=lambda x: x["date"])
    if len(clean) < 20:
        raise RuntimeError("valid daily rows too short")
    adjusted = []
    for row in clean:
        factor = 1.0
        for event in events:
            if event["date"] > int(row["date"]):
                factor *= event["ratio"]
        out = dict(row)
        for key in ("open", "high", "low", "close", "last"):
            out[key] = out[key] * factor
        adjusted.append(out)
    return {
        "code": code, "raw": clean, "adjusted": adjusted,
        "events": len(events), "price_adjust_events": len(adjustments),
        "share_change_events": len(unique_shares), "latest_date": clean[-1]["date"],
    }

def shard_for(code):
    return int(hashlib.sha1(code.encode("utf-8")).hexdigest()[:8], 16) % SHARDS

def main():
    if not SCREENER.exists():
        raise RuntimeError("Missing data/market_screener.csv; run scan_market.py first")
    OUT.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    items, seen = [], set()
    with SCREENER.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            code = str(row.get("insCode", "")).strip()
            if code and code not in seen and row.get("asset_type") in ("share", "fund"):
                seen.add(code)
                items.append(row)
    if not items:
        raise RuntimeError("No eligible instruments found in screener")
    OUT.mkdir(parents=True, exist_ok=True)
    raw_paths = [OUT / ("raw_%02d.csv.gz" % i) for i in range(SHARDS)]
    adj_paths = [OUT / ("adjusted_%02d.csv.gz" % i) for i in range(SHARDS)]
    raw_files = [gzip.open(p, "wt", encoding="utf-8", newline="", compresslevel=6, mtime=0) for p in raw_paths]
    adj_files = [gzip.open(p, "wt", encoding="utf-8", newline="", compresslevel=6, mtime=0) for p in adj_paths]
    raw_writers = [csv.DictWriter(f, fieldnames=FIELDS) for f in raw_files]
    adj_writers = [csv.DictWriter(f, fieldnames=FIELDS) for f in adj_files]
    for writer in raw_writers + adj_writers:
        writer.writeheader()
    ok, failed, latest, total_rows, total_events = [], [], "", 0, 0
    try:
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            futures = {pool.submit(fetch_one, item): item for item in items}
            done = 0
            for future in as_completed(futures):
                item = futures[future]
                done += 1
                try:
                    result = future.result()
                    shard = shard_for(result["code"])
                    raw_writers[shard].writerows(result["raw"])
                    adj_writers[shard].writerows(result["adjusted"])
                    total_rows += len(result["raw"])
                    total_events += result["events"]
                    latest = max(latest, result["latest_date"])
                    ok.append({
                        "insCode": result["code"], "symbol": item.get("symbol", ""),
                        "asset_type": item.get("asset_type", ""),
                        "rows": len(result["raw"]), "latest_date": result["latest_date"],
                        "events": result["events"], "shard": shard,
                    })
                except Exception as exc:
                    failed.append({"insCode": item.get("insCode", ""), "symbol": item.get("symbol", ""), "error": str(exc)[:300]})
                if done % 50 == 0 or done == len(items):
                    print("Progress: %d/%d, success=%d, failed=%d" % (done, len(items), len(ok), len(failed)), flush=True)
    finally:
        for f in raw_files + adj_files:
            f.close()
    ok.sort(key=lambda x: x["symbol"])
    failed.sort(key=lambda x: x["symbol"])
    manifest = {
        "generated_at_utc": datetime.utcnow().isoformat() + "Z",
        "source": "TSETMC CDN API",
        "instruments_requested": len(items),
        "instruments_succeeded": len(ok),
        "instruments_failed": len(failed),
        "raw_rows": total_rows,
        "adjusted_rows": total_rows,
        "corporate_action_events": total_events,
        "latest_date": latest,
        "workers": WORKERS,
        "shards": SHARDS,
        "format": "gzip-compressed CSV; 16 deterministic shards; raw and adjusted OHLCV stored separately",
        "adjustment_method": "TSETMC PriceAdjustList pClosing/pClosingNotAdjusted plus uncovered share-change old/new ratios; events within 7 calendar days are deduplicated",
        "warning": "Adjusted history requires successful retrieval of both corporate-action endpoints for each symbol. Failed instruments are excluded from adjusted shards and listed in failures.json.",
        "instruments": ok,
        "failures": failed,
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print("COMPLETE: requested=%d success=%d failed=%d rows=%d latest=%s" % (len(items), len(ok), len(failed), total_rows, latest))

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("ERROR:", exc, file=sys.stderr)
        raise
