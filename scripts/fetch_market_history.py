#!/usr/bin/env python3
"""Fetch raw and corporate-action-adjusted daily history for all screened shares/funds."""
from __future__ import annotations
import csv, gzip, hashlib, json, math, os, sys, time, urllib.request, subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

BASE = "https://cdn.tsetmc.com/api"
DATA = Path("data")
SCREENER = DATA / "market_screener.csv"
OUT = DATA / "market_history"
SHARDS = 24
WORKERS = 10
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.tsetmc.com/",
}
FIELDS = ["symbol", "name", "asset_type", "insCode", "date", "open", "high", "low", "close", "last", "volume", "value"]

def get_json(url, attempts=2):
    last = None
    for n in range(attempts):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=18) as response:
                return json.loads(response.read().decode("utf-8-sig"))
        except Exception as exc:
            last = exc
            if n + 1 < attempts:
                time.sleep(0.8 * (n + 1))
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
    daily_url = BASE + "/ClosingPrice/GetClosingPriceDailyList/" + code + "/0"
    adjust_url = BASE + "/ClosingPrice/GetPriceAdjustList/" + code
    shares_url = BASE + "/Instrument/GetInstrumentShareChange/" + code
    daily = rows_from(get_json(daily_url), ("closingPriceDaily",))
    if len(daily) < 20:
        raise RuntimeError("daily history too short: " + str(len(daily)))
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
            "open": number(row.get("priceFirst")), "high": number(row.get("priceMax")),
            "low": number(row.get("priceMin")), "close": number(row.get("pClosing")),
            "last": number(row.get("pDrCotVal")), "volume": int(number(row.get("qTotTran5J"))),
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
    return {"code": code, "raw": clean, "adjusted": adjusted, "events": len(events),
            "price_adjust_events": len(adjustments), "share_change_events": len(unique_shares),
            "latest_date": clean[-1]["date"]}

def shard_for(code):
    return int(hashlib.sha1(code.encode("utf-8")).hexdigest()[:8], 16) % SHARDS

def write_batch(batch_no, results, failures):
    raw_tmp = OUT / ("batch_%03d_raw.csv.gz.tmp" % batch_no)
    adj_tmp = OUT / ("batch_%03d_adjusted.csv.gz.tmp" % batch_no)
    raw_final = OUT / ("batch_%03d_raw.csv.gz" % batch_no)
    adj_final = OUT / ("batch_%03d_adjusted.csv.gz" % batch_no)
    meta_tmp = OUT / ("batch_%03d.json.tmp" % batch_no)
    meta_final = OUT / ("batch_%03d.json" % batch_no)
    with gzip.open(raw_tmp, "wt", encoding="utf-8", newline="", compresslevel=3) as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        for result in results:
            writer.writerows(result["raw"])
    with gzip.open(adj_tmp, "wt", encoding="utf-8", newline="", compresslevel=3) as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        for result in results:
            writer.writerows(result["adjusted"])
    meta = {
        "batch": batch_no, "success": len(results), "failed": len(failures),
        "raw_rows": sum(len(x["raw"]) for x in results),
        "events": sum(x["events"] for x in results),
        "instruments": [
            {"insCode": x["code"], "symbol": x["symbol"], "asset_type": x["asset_type"],
             "rows": len(x["raw"]), "latest_date": x["latest_date"], "events": x["events"]}
            for x in results
        ],
        "failures": failures,
        "saved_at_utc": datetime.utcnow().isoformat() + "Z"
    }
    meta_tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(raw_tmp, raw_final)
    os.replace(adj_tmp, adj_final)
    os.replace(meta_tmp, meta_final)
    subprocess.run(["git", "add", str(raw_final), str(adj_final), str(meta_final)], check=True)
    changed = subprocess.run(["git", "diff", "--cached", "--quiet"]).returncode != 0
    if changed:
        subprocess.run(["git", "commit", "-m", "Checkpoint market history batch %03d" % batch_no], check=True)
        subprocess.run(["git", "push", "origin", "HEAD:main"], check=True)
    print("CHECKPOINT SAVED: batch=%d success=%d failed=%d" %
          (batch_no, len(results), len(failures)), flush=True)


def main():
    if not SCREENER.exists():
        raise RuntimeError("Missing data/market_screener.csv; run scan_market.py first")
    OUT.mkdir(parents=True, exist_ok=True)
    items, seen = [], set()
    with SCREENER.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            code = str(row.get("insCode", "")).strip()
            if code and code not in seen and row.get("asset_type") in ("share", "fund") and not str(row.get("symbol", "")).strip().endswith("3"):
                seen.add(code)
                items.append(row)
    if not items:
        raise RuntimeError("No eligible instruments found in screener")

    batch_size = 50
    batches = [items[i:i + batch_size] for i in range(0, len(items), batch_size)]
    completed, all_ok, all_failed = set(), [], []
    for batch_no, batch in enumerate(batches):
        meta_path = OUT / ("batch_%03d.json" % batch_no)
        raw_path = OUT / ("batch_%03d_raw.csv.gz" % batch_no)
        adj_path = OUT / ("batch_%03d_adjusted.csv.gz" % batch_no)
        if meta_path.exists() and raw_path.exists() and adj_path.exists():
            try:
                old = json.loads(meta_path.read_text(encoding="utf-8"))
                if int(old.get("failed", 1)) == 0:
                    completed.add(batch_no)
                    all_ok.extend(old.get("instruments", []))
                    print("RESUME: batch %d/%d already saved" % (batch_no + 1, len(batches)), flush=True)
                    continue
            except Exception:
                pass

        results, failures = [], []
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            futures = {pool.submit(fetch_one, item): item for item in batch}
            done = 0
            for future in as_completed(futures):
                item = futures[future]
                done += 1
                try:
                    result = future.result()
                    result["symbol"] = str(item.get("symbol", ""))
                    result["asset_type"] = str(item.get("asset_type", ""))
                    results.append(result)
                except Exception as exc:
                    failures.append({"insCode": item.get("insCode", ""),
                                     "symbol": item.get("symbol", ""),
                                     "error": str(exc)[:300]})
                if done % 10 == 0 or done == len(batch):
                    print("Batch %d/%d: %d/%d success=%d failed=%d" %
                          (batch_no + 1, len(batches), done, len(batch),
                           len(results), len(failures)), flush=True)

        results.sort(key=lambda x: x["symbol"])
        failures.sort(key=lambda x: x["symbol"])
        write_batch(batch_no, results, failures)
        all_ok.extend([
            {"insCode": x["code"], "symbol": x["symbol"], "asset_type": x["asset_type"],
             "rows": len(x["raw"]), "latest_date": x["latest_date"], "events": x["events"]}
            for x in results
        ])
        all_failed.extend(failures)
        completed.add(batch_no)

    # Rebuild the final manifest from durable batch checkpoints.
    all_ok, all_failed = [], []
    total_rows = total_events = 0
    latest = ""
    for batch_no in range(len(batches)):
        meta_path = OUT / ("batch_%03d.json" % batch_no)
        if not meta_path.exists():
            continue
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        all_ok.extend(meta.get("instruments", []))
        all_failed.extend(meta.get("failures", []))
        total_rows += int(meta.get("raw_rows", 0))
        total_events += int(meta.get("events", 0))
        for instrument in meta.get("instruments", []):
            latest = max(latest, instrument.get("latest_date", ""))
    all_ok.sort(key=lambda x: x["symbol"])
    all_failed.sort(key=lambda x: x["symbol"])
    manifest = {
        "generated_at_utc": datetime.utcnow().isoformat() + "Z",
        "source": "TSETMC CDN API",
        "instruments_requested": len(items),
        "instruments_succeeded": len(all_ok),
        "instruments_failed": len(all_failed),
        "raw_rows": total_rows, "adjusted_rows": total_rows,
        "corporate_action_events": total_events, "latest_date": latest,
        "workers": WORKERS, "batch_size": batch_size,
        "format": "Per-batch gzip CSV checkpoints; raw and adjusted OHLCV stored separately",
        "adjustment_method": "TSETMC PriceAdjustList pClosing/pClosingNotAdjusted plus uncovered share-change old/new ratios; events within 7 calendar days are deduplicated",
        "warning": "Failed instruments are listed in failures; rerun retries any batch containing failures.",
        "instruments": all_ok, "failures": all_failed
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    subprocess.run(["git", "add", str(OUT / "manifest.json")], check=True)
    if subprocess.run(["git", "diff", "--cached", "--quiet"]).returncode != 0:
        subprocess.run(["git", "commit", "-m", "Finalize market history manifest"], check=True)
        subprocess.run(["git", "push", "origin", "HEAD:main"], check=True)
    print("COMPLETE: requested=%d success=%d failed=%d rows=%d latest=%s" %
          (len(items), len(all_ok), len(all_failed), total_rows, latest), flush=True)

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("ERROR:", exc, file=sys.stderr)
        raise
