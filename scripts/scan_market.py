#!/usr/bin/env python3
"""Build a whole-market TSETMC snapshot and retail-flow screener."""
from __future__ import annotations
import csv, json, math, sys, time, urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE = "https://cdn.tsetmc.com/api"
OUT = Path("data")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.tsetmc.com/",
}

def get_json(url, attempts=4):
    err = None
    for n in range(attempts):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=40) as r:
                raw = r.read().decode("utf-8-sig")
            data = json.loads(raw)
            if isinstance(data, dict) and any(k in raw for k in ("General Error Detected", "دسترسی شما", "مسدود")):
                raise RuntimeError("TSETMC access blocked")
            return data
        except Exception as e:
            err = e
            if n < attempts - 1:
                time.sleep(2 ** n)
    raise RuntimeError(f"GET failed: {url}: {err}")

def find_list(obj, names):
    if isinstance(obj, list):
        return obj
    if isinstance(obj, dict):
        for k in names:
            if isinstance(obj.get(k), list):
                return obj[k]
        for v in obj.values():
            if isinstance(v, list) and (not v or isinstance(v[0], dict)):
                return v
    return []

def num(d, *keys):
    for k in keys:
        v = d.get(k)
        try:
            if v is not None and v != "":
                return float(v)
        except (TypeError, ValueError):
            pass
    return 0.0

def first(d, *keys, default=""):
    for k in keys:
        if d.get(k) not in (None, ""):
            return d[k]
    return default

def classify_instrument(ins, item, symbol, name):
    """Keep only ordinary listed shares (TSE/IFB) and investment funds."""
    isin = str(first(ins, "insID", "cIsin", "isin", default=first(item, "insID", "cIsin", "isin"))).upper().strip()
    sym = str(symbol).strip()
    if sym.endswith("3"):
        return None
    label = (str(name) + " " + sym).replace("\\u200c", " ")
    # Rights, options, futures and other derivatives are explicitly out of scope.
    derivative_terms = ("حق تقدم", "اختیار خرید", "اختیار فروش", "قرارداد آتی", "آتی", "گواهی سپرده")
    if sym.endswith("ح") or any(term in label for term in derivative_terms) or isin.startswith("IRO9"):
        return None
    if isin.startswith(("IRO1", "IRO3")):
        return "share"
    if isin.startswith("IRT") or "صندوق" in label:
        return "fund"
    return None

def main():
    OUT.mkdir(exist_ok=True)
    # Market watch endpoint returns the live/latest market snapshot.
    params = ["market=0"] + [f"paperTypes[{i}]={i+1}" for i in range(9)]
    params += ["withBestLimits=false", "hEven=0", "RefID=0"]
    watch_url = BASE + "/ClosingPrice/GetMarketWatch?" + "&".join(params)
    watch_payload = get_json(watch_url)
    watch = find_list(watch_payload, ("marketwatch", "marketWatch", "closingPriceInfo"))
    if not watch:
        raise RuntimeError("MarketWatch response had no instrument rows; keys=" +
                           ",".join(watch_payload.keys()) if isinstance(watch_payload, dict)
                           else "MarketWatch response had no instrument rows")
    # Temporary schema diagnostic: expose the first raw row's key/value pairs in Actions logs.
    if watch and isinstance(watch[0], dict):
        print("MARKETWATCH_SAMPLE_ROW:", json.dumps(watch[0], ensure_ascii=False)[:5000])
    # Bulk client-type endpoint avoids thousands of per-symbol requests.
    ct_payload = get_json(BASE + "/ClientType/GetClientTypeAll")
    client_rows = find_list(ct_payload, ("clientTypeAllDto", "clientTypeAll", "clientType"))
    client_by_code = {}
    for row in client_rows:
        code = str(first(row, "insCode", "inscode", "ins_code"))
        if code:
            client_by_code[code] = row

    output = []
    excluded = {"outside_scope": 0, "rights_and_derivatives": 0, "after_hours_suffix_3": 0}
    for item in watch:
        if not isinstance(item, dict):
            continue
        info = item.get("instrument") if isinstance(item.get("instrument"), dict) else {}
        price = item.get("closingPriceInfo") if isinstance(item.get("closingPriceInfo"), dict) else item
        ins = info or item
        code = str(first(ins, "insCode", "inscode", "ins_code", default=first(price, "insCode", "inscode", "ins_code")))
        if not code or code == "0":
            continue
        ct = client_by_code.get(code, {})
        symbol = first(ins, "lva", "lVal18AFC", "lVal18", "symbol", "ticker", default=first(ins, "lvc", "lVal30", "name"))
        name = first(ins, "lvc", "lVal30", "name", "companyName", default=symbol)
        asset_type = classify_instrument(ins, item, symbol, name)
        if asset_type is None:
            isin = str(first(ins, "insID", "cIsin", "isin", default=first(item, "insID", "cIsin", "isin"))).upper()
            label = (str(name) + " " + str(symbol)).replace("\\u200c", " ")
            if str(symbol).strip().endswith("3"):
                excluded["after_hours_suffix_3"] += 1
            elif str(symbol).strip().endswith("ح") or isin.startswith("IRO9") or any(t in label for t in ("حق تقدم", "اختیار خرید", "اختیار فروش", "قرارداد آتی", "آتی", "گواهی سپرده")):
                excluded["rights_and_derivatives"] += 1
            else:
                excluded["outside_scope"] += 1
            continue
        # Use only documented TSETMC field names. Fuzzy aliases caused
        # yesterday-price and close-price misreads in earlier snapshots.
        close = num(price, "pClosing")
        last = num(price, "pDrCotVal")
        yesterday = num(price, "priceYesterday")
        volume = num(price, "qTotTran5J")
        value = num(price, "qTotCap")
        trades = num(price, "zTotTran")
        buy_iv = num(ct, "buy_I_Volume")
        sell_iv = num(ct, "sell_I_Volume")
        buy_ic = num(ct, "buy_I_Count")
        sell_ic = num(ct, "sell_I_Count")
        buy_nv = num(ct, "buy_N_Volume")
        sell_nv = num(ct, "sell_N_Volume")
        buy_value = num(ct, "buy_I_Value")
        sell_value = num(ct, "sell_I_Value")
        buy_total = buy_iv + buy_nv
        sell_total = sell_iv + sell_nv
        client_type_valid = bool(ct) and buy_ic > 0 and sell_ic > 0 and volume > 0 and (
            abs(buy_total - volume) / volume <= 0.10 and
            abs(sell_total - volume) / volume <= 0.10
        )
        net_real = (buy_value - sell_value) if client_type_valid and ("buy_I_Value" in ct or "sell_I_Value" in ct) else (
            (buy_iv - sell_iv) * close if client_type_valid and close > 0 else None
        )
        buyer_power = ((buy_iv / buy_ic) / (sell_iv / sell_ic)) if client_type_valid and buy_iv > 0 and sell_iv > 0 else None
        raw_change_pct = ((close / yesterday) - 1) * 100 if close > 0 and yesterday > 0 else None
        change_pct = raw_change_pct if raw_change_pct is not None and abs(raw_change_pct) <= 15 else None
        base_vol = num(ins, "baseVol", "baseVolume") or num(item, "baseVol", "baseVolume")
        base_ratio = volume / base_vol if base_vol > 0 else None
        # Ranking is deliberately disabled until source fields pass live validation.
        score = ""
        output.append({
            "symbol": symbol, "name": name, "asset_type": asset_type, "insCode": code,
            "market": first(ins, "cgrValCotTitle", "flowTitle", "marketName"),
            "sector": first(ins, "lSecValTitle", "lSecVal", "sectorName"),
            "close_rial": close, "last_rial": last, "change_pct": round(change_pct, 3) if change_pct is not None else "",
            "volume": int(volume), "trade_count": int(trades), "value_rial": int(value),
            "individual_buy_volume": int(buy_iv), "individual_sell_volume": int(sell_iv),
            "individual_buy_count": int(buy_ic), "individual_sell_count": int(sell_ic),
            "legal_buy_volume": int(buy_nv), "legal_sell_volume": int(sell_nv),
            "net_individual_value_rial": int(net_real) if net_real is not None else "",
            "buyer_power": round(buyer_power, 3) if buyer_power is not None else "",
            "base_volume": int(base_vol), "volume_to_base": round(base_ratio, 3) if base_ratio is not None else "",
            "screen_score": score,
            "client_type_available": bool(ct),
            "client_type_valid": client_type_valid,
        })

    named = sum(1 for x in output if x["symbol"])
    priced = sum(1 for x in output if x["close_rial"] > 0)
    if not output or named < len(output) * 0.5 or priced < len(output) * 0.5:
        sample = list(watch[0].keys()) if watch and isinstance(watch[0], dict) else []
        raise RuntimeError(f"MarketWatch field mapping validation failed: rows={len(output)}, named={named}, priced={priced}, sample_keys={sample}")
    # Do not present a ranking while price/client-type fields are unverified.
    output.sort(key=lambda x: x["value_rial"], reverse=True)
    stamp = datetime.now(timezone.utc).isoformat()
    csv_path = OUT / "market_screener.csv"
    fields = list(output[0].keys())
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(output)
    summary = {
        "generated_at_utc": stamp,
        "source": "TSETMC CDN API",
        "marketwatch_rows": len(watch),
        "symbols_saved": len(output),
        "shares_saved": sum(1 for x in output if x["asset_type"] == "share"),
        "funds_saved": sum(1 for x in output if x["asset_type"] == "fund"),
        "excluded": excluded,
        "scope": "Only ordinary TSE/IFB shares and investment funds; rights and derivatives excluded.",
        "symbols_with_name": named,
        "symbols_with_price": priced,
        "client_type_rows": len(client_rows),
        "symbols_with_client_type": sum(1 for x in output if x["client_type_available"]),
        "symbols_with_valid_client_type": sum(1 for x in output if x["client_type_valid"]),
        "symbols_with_valid_change_pct": sum(1 for x in output if x["change_pct"] != ""),
        "symbols_with_base_volume": sum(1 for x in output if x["base_volume"] > 0),
        "ranking_enabled": False,
        "ranking_note": "Disabled pending live validation of price, client-type and base-volume field mappings.",
        "limitations": [
            "Abnormal volume versus 20-day average is not yet calculated.",
            "Laggard detection requires multi-day history comparison and is not yet included.",
            "Client-type metrics are unavailable for symbols missing from the bulk client-type response.",
            "MarketWatch may represent latest available snapshot outside trading hours.",
            "Instrument scope is filtered by ISIN prefix and Persian instrument-name/symbol exclusions; verify coverage against TSETMC instrument master.",
            "Price change is omitted when the computed daily move exceeds 15% pending source-field validation.",
            "Retail flow and buyer power are omitted unless both client sides reconcile to market volume within 10% and both buyer counts are positive.",
            "Screen score is intentionally blank; no investment ranking is published until live field mappings are verified."
        ],
        "schema_diagnostics": {
            "marketwatch_sample_keys": list(watch[0].keys()) if watch and isinstance(watch[0], dict) else [],
            "instrument_sample_keys": list((watch[0].get("instrument") or {}).keys()) if watch and isinstance(watch[0], dict) and isinstance(watch[0].get("instrument"), dict) else [],
            "price_sample_keys": list((watch[0].get("closingPriceInfo") or {}).keys()) if watch and isinstance(watch[0], dict) and isinstance(watch[0].get("closingPriceInfo"), dict) else [],
            "client_type_sample_keys": list(client_rows[0].keys()) if client_rows and isinstance(client_rows[0], dict) else []
        },
        "top_20": output[:20],
    }
    (OUT / "market_screener_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"OK: market rows={len(watch)}, saved symbols={len(output)}, client-type rows={len(client_rows)}, matched={summary['symbols_with_client_type']}")
    print(f"CSV: {csv_path}")
    print("Top 10:")
    for row in output[:10]:
        print(row["symbol"], row["change_pct"], row["net_individual_value_rial"], row["buyer_power"], row["screen_score"])

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("ERROR:", exc, file=sys.stderr)
        raise
