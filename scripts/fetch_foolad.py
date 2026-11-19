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
CHART_URL = "https://members.tsetmc.com/tsev2/chart/data/Financial.aspx"
CHART_FIELDS = ["date", "pmax", "pmin", "pf", "pl", "tvol", "pc"]

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

def get_chart_history(adjusted: bool):
    """Fetch TSETMC chart history. a=1 is adjusted; a=0 is unadjusted."""
    from urllib.parse import urlencode
    url = CHART_URL + "?" + urlencode({
        "i": INS_CODE, "t": "ph", "a": "1" if adjusted else "0"
    })
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; IranStockAnalysis/1.0)",
        "Accept": "text/plain, */*",
        "Referer": "https://www.tsetmc.com/",
    }
    last_error = None
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=45) as response:
                body = response.read().decode("utf-8-sig").strip()
            rows = []
            for line in body.split(";"):
                parts = [part.strip() for part in line.split(",")]
                if len(parts) < len(CHART_FIELDS) or not parts[0].isdigit():
                    continue
                rows.append(dict(zip(CHART_FIELDS, parts[:len(CHART_FIELDS)])))
            if not rows:
                raise ValueError("Empty/unrecognized chart response; adjusted=" + str(adjusted) + "; body=" + body[:300])
            return url, rows
        except Exception as exc:
            last_error = exc
            if attempt < 3:
                time.sleep(2 ** attempt)
    raise RuntimeError("Chart history failed; adjusted=" + str(adjusted) + ": " + str(last_error))

def save_chart_csv(path: Path, rows):
    ordered = sorted({str(r["date"]): r for r in rows}.values(), key=lambda r: r["date"])
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CHART_FIELDS)
        writer.writeheader()
        writer.writerows(ordered)
    return ordered

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

    # Keep raw and adjusted chart series separately; never overwrite execution prices.
    raw_chart_url, raw_chart_rows = get_chart_history(adjusted=False)
    adjusted_chart_url, adjusted_chart_rows = get_chart_history(adjusted=True)
    raw_chart = save_chart_csv(OUT_DIR / "foolad_chart_raw.csv", raw_chart_rows)
    adjusted_chart = save_chart_csv(OUT_DIR / "foolad_chart_adjusted.csv", adjusted_chart_rows)
    if len(adjusted_chart) < 100 or len(raw_chart) < 100:
        raise ValueError("Chart history validation failed: fewer than 100 records")
    if len({r["date"] for r in adjusted_chart}) != len(adjusted_chart):
        raise ValueError("Duplicate dates remain in adjusted chart history")

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
            "chart_history": {
                "raw_url": raw_chart_url,
                "adjusted_url": adjusted_chart_url,
                "raw_rows_saved": len(raw_chart),
                "adjusted_rows_saved": len(adjusted_chart),
                "adjustment_parameter": "a=1",
                "adjusted_fields": ["pmax", "pmin", "pf", "pl", "pc"],
                "volume_field": "tvol",
                "note": "Adjusted chart prices are stored separately; raw daily API prices remain unchanged. Validate against a trusted adjusted chart before automated signals."
            },
        }, f, ensure_ascii=False, indent=2)

    print(f"OK: {len(rows)} rows received; {len(ordered)} unique dates saved.")
    print(f"CSV: {CSV_PATH}")
    print(f"Instrument metadata: {META_PATH}")
    print("Latest date:", ordered[-1].get("date"))
    print("Chart history: raw={}, adjusted={}".format(len(raw_chart), len(adjusted_chart)))
    print("Adjusted chart CSV:", OUT_DIR / "foolad_chart_adjusted.csv")

if __name__ == "__main__":
    try:
        main()
    except (Exception, KeyboardInterrupt) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
