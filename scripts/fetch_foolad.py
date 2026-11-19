#!/usr/bin/env python3
"""Download daily price history for Foolad (فولاد) from TSETMC."""
from __future__ import annotations

import csv
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

INS_CODE = os.getenv("TSETMC_INS_CODE", "46348559193224090")
SYMBOL = os.getenv("TSETMC_SYMBOL", "فولاد")
BASE = "https://cdn.tsetmc.com/api"
OUT_DIR = Path("data")
CSV_PATH = OUT_DIR / "foolad_daily.csv"
META_PATH = OUT_DIR / "foolad_instrument.json"

FIELDS = [
    "date", "insCode", "priceFirst", "pClosing", "pDrCotVal",
    "priceMin", "priceMax", "priceYesterday", "priceChange",
    "zTotTran", "qTotTran5J", "qTotCap",
]

def get_json(url: str, attempts: int = 4):
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; IranStockAnalysis/1.0)",
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://www.tsetmc.com/",
    }
    last_error = None
    for attempt in range(attempts):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=35) as response:
                if response.status != 200:
                    raise RuntimeError(f"HTTP {response.status}: {url}")
                return json.loads(response.read().decode("utf-8-sig"))
        except Exception as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"Request failed after {attempts} attempts: {url}: {last_error}")

def unwrap_object(payload):
    if isinstance(payload, list):
        return payload[0] if payload and isinstance(payload[0], dict) else {}
    if not isinstance(payload, dict):
        return {}
    # TSETMC responses sometimes wrap the actual object in a named property.
    for key in ("instrumentInfo", "instrument", "instrumentInfoData"):
        value = payload.get(key)
        if isinstance(value, dict):
            return value
        if isinstance(value, list) and value and isinstance(value[0], dict):
            return value[0]
    return payload

def extract_daily_rows(payload):
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        rows = None
        for key in ("closingPriceDaily", "closingPriceDailyList", "closingPriceDailyData"):
            if isinstance(payload.get(key), list):
                rows = payload[key]
                break
        if rows is None:
            # Fallback for a response whose root contains a list-valued property.
            rows = next((v for v in payload.values()
                         if isinstance(v, list) and (not v or isinstance(v[0], dict))), None)
        if rows is None:
            raise ValueError("Could not find daily rows in TSETMC response. Root keys: "
                             + ", ".join(payload.keys()))
    else:
        raise ValueError(f"Unexpected daily response type: {type(payload).__name__}")
    return [row for row in rows if isinstance(row, dict)]

def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    info_url = f"{BASE}/Instrument/GetInstrumentInfo/{INS_CODE}"
    daily_url = f"{BASE}/ClosingPrice/GetClosingPriceDailyList/{INS_CODE}/0"

    info_payload = get_json(info_url)
    info = unwrap_object(info_payload)
    if not info:
        raise ValueError("Instrument information response was empty")

    daily_payload = get_json(daily_url)
    rows = extract_daily_rows(daily_payload)
    if not rows:
        raise ValueError("TSETMC returned no daily price rows")

    # Preserve all previously collected dates and update overlapping records.
    merged = {}
    if CSV_PATH.exists():
        with CSV_PATH.open("r", encoding="utf-8-sig", newline="") as f:
            for old in csv.DictReader(f):
                date = old.get("date") or old.get("dEven")
                if date:
                    merged[str(date)] = old

    for raw in rows:
        date = raw.get("dEven") or raw.get("date") or raw.get("dEvenJalali")
        if date is None:
            continue
        record = {"date": str(date)}
        for field in FIELDS[1:]:
            value = raw.get(field)
            record[field] = "" if value is None else value
        # Keep a stable symbol label in the output.
        record["symbol"] = SYMBOL
        merged[str(date)] = record

    if not merged:
        raise ValueError("No rows with a recognizable trading date were found")

    output_fields = ["symbol"] + FIELDS
    ordered = sorted(merged.values(), key=lambda r: str(r.get("date", "")))
    with CSV_PATH.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=output_fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(ordered)

    with META_PATH.open("w", encoding="utf-8") as f:
        json.dump({
            "symbol": SYMBOL,
            "insCode": INS_CODE,
            "instrument_info": info,
            "daily_api": daily_url,
            "rows_in_latest_response": len(rows),
            "rows_saved": len(ordered),
        }, f, ensure_ascii=False, indent=2)

    print(f"OK: {len(rows)} rows received; {len(ordered)} unique dates saved.")
    print(f"CSV: {CSV_PATH}")
    print(f"Instrument metadata: {META_PATH}")
    print("Latest date:", ordered[-1].get("date"))

if __name__ == "__main__":
    try:
        main()
    except (Exception, KeyboardInterrupt) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
