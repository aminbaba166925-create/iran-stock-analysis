#!/usr/bin/env python3
"""V2 recovery: refresh MarketWatch, normalize symbols, inspect nested search rows, checkpoint each instrument."""
import csv, gzip, json, os, re, subprocess, sys, urllib.parse, unicodedata
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, "scripts")
import scan_market
import fetch_market_history_next100_v4 as history

# شارپیلن is already archived (371 rows) and is intentionally not fetched again.
SYMBOLS = ["بایکا","پردیس","زندگی","بکهنوج","بشام","جوین","حیات","خکاوه","خودکفا",
           "قیسنو","کصدف","کقزوی","کهرام","لزما","لپیام","لکما","شکبیر"]
OUT = Path("data/market_history_recovery_v2")
BRANCH = os.getenv("CHECKPOINT_BRANCH", "market-history-user33-checkpoints")
BASE = scan_market.BASE
FIELDS = history.FIELDS
MIN_DEFAULT = 20

def norm(value):
    s = unicodedata.normalize("NFKC", str(value or ""))
    s = s.replace("ي", "ی").replace("ك", "ک").replace("ۀ", "ه")
    s = s.replace("\u200c", "").replace("\u200f", "").replace("\u200e", "")
    return re.sub(r"\s+", "", s).strip()

def checkpoint(paths, message):
    subprocess.run(["git", "add", *[str(p) for p in paths]], check=True)
    if subprocess.run(["git", "diff", "--cached", "--quiet"]).returncode == 0:
        return
    subprocess.run(["git", "commit", "-m", message], check=True)
    last = None
    for _ in range(6):
        try:
            subprocess.run(["git", "push", "origin", "HEAD:" + BRANCH], check=True)
            return
        except subprocess.CalledProcessError as exc:
            last = exc
            subprocess.run(["git", "fetch", "origin", BRANCH], check=True)
            subprocess.run(["git", "rebase", "FETCH_HEAD"], check=True)
    raise RuntimeError("checkpoint push failed") from last

def recursive_dicts(obj):
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            yield from recursive_dicts(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from recursive_dicts(value)

def unpack_instrument(row):
    candidates = [row]
    for key in ("instrument", "instrumentInfo", "instrumentDto", "ins", "item"):
        value = row.get(key)
        if isinstance(value, dict):
            candidates.insert(0, value)
    return candidates

def make_item(ins, row, requested):
    symbol = str(scan_market.first(ins, "lva", "lVal18AFC", "lVal18", "symbol", "ticker",
                                  default=scan_market.first(row, "lva", "lVal18AFC", "lVal18", "symbol", "ticker"))).strip()
    name = str(scan_market.first(ins, "lvc", "lVal30", "name", "companyName",
                                default=scan_market.first(row, "lvc", "lVal30", "name", "companyName", default=symbol))).strip()
    code = str(scan_market.first(ins, "insCode", "inscode", "ins_code",
                                default=scan_market.first(row, "insCode", "inscode", "ins_code"))).strip()
    if not code or code == "0" or norm(symbol) != norm(requested):
        return None
    typ = str(row.get("asset_type") or ins.get("asset_type") or "")
    if typ not in ("share", "fund"):
        typ = scan_market.classify_instrument(ins, row, symbol, name)
    if not typ:
        return None
    base_vol = scan_market.num(ins, "baseVol", "baseVolume", "bv") or scan_market.num(row, "base_volume", "baseVol", "baseVolume", "bv")
    return {"symbol": requested, "name": name, "insCode": code,
            "baseVol": base_vol, "asset_type": typ, "matched_symbol": symbol}

def discover_from_screener(sym):
    path = Path("data/market_screener.csv")
    found = {}
    if path.exists():
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                if norm(row.get("symbol")) == norm(sym):
                    item = make_item(row, row, sym)
                    if item:
                        found[item["insCode"]] = item
    return list(found.values())

def discover_from_search(sym):
    q = urllib.parse.quote(sym, safe="")
    urls = [BASE + "/Instrument/GetInstrumentSearch/" + q,
            BASE + "/Instrument/GetInstrumentSearch/" + q + "/0",
            BASE + "/Instrument/GetInstrumentSearch?query=" + q,
            BASE + "/Instrument/GetInstrumentSearch?symbol=" + q]
    found, errors = {}, []
    for url in urls:
        try:
            payload = scan_market.get_json(url, attempts=2)
            for row in recursive_dicts(payload):
                for ins in unpack_instrument(row):
                    item = make_item(ins, row, sym)
                    if item:
                        found[item["insCode"]] = item
        except Exception as exc:
            errors.append(str(exc)[:160])
    if len(found) > 1:
        raise RuntimeError("ambiguous exact symbol search; insCodes=" + ",".join(sorted(found)))
    return list(found.values()), errors

def discover(sym):
    screener = discover_from_screener(sym)
    if len(screener) == 1:
        return screener[0], {"source": "refreshed_market_screener"}
    if len(screener) > 1:
        raise RuntimeError("multiple exact matches in refreshed MarketWatch screener")
    search, errors = discover_from_search(sym)
    if len(search) == 1:
        return search[0], {"source": "recursive_instrument_search", "search_errors": errors}
    return None, {"source": "none", "search_errors": errors}

def main():
    OUT.mkdir(parents=True, exist_ok=True)
    results, unresolved, failures = [], [], []
    for n, sym in enumerate(SYMBOLS, 1):
        stem = f"{n:02d}_{sym}"
        meta, raw, adj = OUT/(stem+".json"), OUT/(stem+"_raw.csv.gz"), OUT/(stem+"_adjusted.csv.gz")
        if meta.exists():
            try:
                old = json.loads(meta.read_text(encoding="utf-8"))
                if old.get("success") and raw.exists() and adj.exists():
                    results.append(old)
                    print(f"ALREADY_SAVED {sym}", flush=True)
                    continue
            except Exception:
                pass
        try:
            item, discovery = discover(sym)
            if not item:
                rec = {"success": False, "status": "not_found_after_marketwatch_and_recursive_search",
                       "ordinal": n, "symbol": sym, "discovery": discovery,
                       "checked_at_utc": datetime.now(timezone.utc).isoformat()}
                meta.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
                checkpoint([meta], f"Record v2 unresolved symbol {sym}")
                unresolved.append(sym)
                print(f"UNRESOLVED {sym}", flush=True)
                continue
            # شکبیر previously had only 8 rows; retain them as limited history.
            minimum = 1 if norm(sym) == norm("شکبیر") else MIN_DEFAULT
            data = history.fetch_one(item, min_rows=minimum)
            for path, key in ((raw, "raw"), (adj, "adjusted")):
                with gzip.open(path, "wt", encoding="utf-8", newline="", compresslevel=3) as handle:
                    writer = csv.DictWriter(handle, fieldnames=FIELDS)
                    writer.writeheader()
                    writer.writerows(data[key])
            count = len(data["raw"])
            rec = {"success": True, "status": "limited_history" if count < MIN_DEFAULT else "complete",
                   "ordinal": n, "symbol": sym, "matched_symbol": item.get("matched_symbol"),
                   "insCode": item["insCode"], "baseVol": item["baseVol"], "asset_type": item["asset_type"],
                   "rows": count, "latest_date": data["latest_date"], "events": data["events"],
                   "discovery": discovery, "saved_at_utc": datetime.now(timezone.utc).isoformat()}
            meta.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
            checkpoint([raw, adj, meta], f"Save v2 recovered history {sym}")
            results.append(rec)
            print(f"SUCCESS {sym} rows={count} status={rec['status']}", flush=True)
        except Exception as exc:
            rec = {"success": False, "ordinal": n, "symbol": sym, "error": str(exc)[:500],
                   "checked_at_utc": datetime.now(timezone.utc).isoformat()}
            meta.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
            checkpoint([meta], f"Record v2 recovery error {sym}")
            failures.append(rec)
            print(f"FAILED {sym}: {str(exc)[:250]}", flush=True)
    manifest = {"generated_at_utc": datetime.now(timezone.utc).isoformat(),
                "requested": len(SYMBOLS), "succeeded": len(results), "unresolved": unresolved,
                "failed": failures, "results": results,
                "note": "Success can be limited_history for symbols with fewer than 20 available daily rows."}
    path = OUT/"recovery_v2_manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    checkpoint([path], "Finalize v2 symbol history recovery")
    print("FINAL " + json.dumps(manifest, ensure_ascii=False), flush=True)

if __name__ == "__main__":
    main()
