#!/usr/bin/env python3
"""Independent incremental history collector for instruments with base volume exactly 1."""
from __future__ import annotations
import csv, gzip, json, os, subprocess, sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "scripts")
import scan_market
import fetch_market_history_next100_v4 as history

BASE = Path("data")
OUT = BASE / "market_history_basevol1"
MASTER = BASE / "market_history_master" / "eligible_codes.json"
CHECKPOINT_BRANCH = os.getenv("CHECKPOINT_BRANCH", "market-history-basevol1-checkpoints")
BATCH_SIZE = 10
WORKERS = 4

def main():
    if not MASTER.exists():
        raise RuntimeError("Missing master eligible_codes.json; refusing to duplicate existing history")
    payload = json.loads(MASTER.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("insCodes"), list) or len(payload["insCodes"]) < 100:
        raise RuntimeError("Invalid or unexpectedly small master exclusion list")
    excluded = {str(x).strip() for x in payload["insCodes"] if str(x).strip()}

    params = ["market=0"] + [f"paperTypes[{i}]={i+1}" for i in range(9)]
    params += ["withBestLimits=false", "hEven=0", "RefID=0"]
    watch = scan_market.get_json(scan_market.BASE + "/ClosingPrice/GetMarketWatch?" + "&".join(params))
    rows = scan_market.find_list(watch, ("marketwatch", "marketWatch", "closingPriceInfo"))
    if not rows:
        raise RuntimeError("MarketWatch returned no rows")

    candidates, seen = [], set()
    skipped = {"suffix3": 0, "already_archived": 0, "not_base_volume_1": 0, "out_of_scope": 0}
    for item in rows:
        if not isinstance(item, dict):
            continue
        ins = item.get("instrument") if isinstance(item.get("instrument"), dict) else {}
        price = item.get("closingPriceInfo") if isinstance(item.get("closingPriceInfo"), dict) else item
        source = ins or item
        symbol = str(scan_market.first(source, "lva", "lVal18AFC", "lVal18", "symbol", "ticker",
                        default=scan_market.first(source, "lvc", "lVal30", "name"))).strip()
        name = str(scan_market.first(source, "lvc", "lVal30", "name", "companyName", default=symbol))
        code = str(scan_market.first(source, "insCode", "inscode", "ins_code",
                        default=scan_market.first(price, "insCode", "inscode", "ins_code"))).strip()
        if not code or code == "0" or code in seen:
            continue
        if symbol.endswith("3"):
            skipped["suffix3"] += 1
            continue
        asset_type = scan_market.classify_instrument(source, item, symbol, name)
        if asset_type not in ("share", "fund"):
            skipped["out_of_scope"] += 1
            continue
        if code in excluded:
            skipped["already_archived"] += 1
            continue
        # Base volume is an instrument master attribute; do not infer it from turnover/volume.
        base_vol = scan_market.num(source, "baseVol", "baseVolume")
        if not base_vol:
            base_vol = scan_market.num(item, "baseVol", "baseVolume")
        if base_vol != 1:
            skipped["not_base_volume_1"] += 1
            continue
        seen.add(code)
        candidates.append({"symbol": symbol, "name": name, "asset_type": asset_type, "insCode": code})
    candidates.sort(key=lambda x: (x["asset_type"], x["symbol"]))
    print("BASEVOL1 UNIVERSE: eligible=%d archived_excluded=%d candidates=%d skipped=%s" %
          (len(rows), len(excluded), len(candidates), json.dumps(skipped, ensure_ascii=False)), flush=True)
    if not candidates:
        raise RuntimeError("No unseen base-volume-1 instruments found. Check live baseVol field mapping before changing filters.")

    OUT.mkdir(parents=True, exist_ok=True)
    fields = history.FIELDS
    summaries, failures = [], []
    batches = [candidates[i:i+BATCH_SIZE] for i in range(0, len(candidates), BATCH_SIZE)]
    for batch_no, batch in enumerate(batches):
        raw_path = OUT / ("batch_%03d_raw.csv.gz" % batch_no)
        adj_path = OUT / ("batch_%03d_adjusted.csv.gz" % batch_no)
        meta_path = OUT / ("batch_%03d.json" % batch_no)
        results, batch_failures = [], []
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            futures = {pool.submit(history.fetch_one, item): item for item in batch}
            for future in as_completed(futures):
                item = futures[future]
                try:
                    result = future.result()
                    result["symbol"] = item["symbol"]
                    result["asset_type"] = item["asset_type"]
                    results.append(result)
                except Exception as exc:
                    batch_failures.append({"insCode": item["insCode"], "symbol": item["symbol"], "error": str(exc)[:300]})
        results.sort(key=lambda x: x["symbol"])
        batch_failures.sort(key=lambda x: x["symbol"])
        for path, key in ((raw_path, "raw"), (adj_path, "adjusted")):
            with gzip.open(path, "wt", encoding="utf-8", newline="", compresslevel=3) as f:
                writer = csv.DictWriter(f, fieldnames=fields)
                writer.writeheader()
                for result in results:
                    writer.writerows(result[key])
        meta = {
            "batch": batch_no, "success": len(results), "failed": len(batch_failures),
            "raw_rows": sum(len(x["raw"]) for x in results),
            "corporate_action_events": sum(x["events"] for x in results),
            "instruments": [{"insCode": x["code"], "symbol": x["symbol"], "asset_type": x["asset_type"],
                             "rows": len(x["raw"]), "latest_date": x["latest_date"], "events": x["events"]}
                            for x in results],
            "failures": batch_failures, "saved_at_utc": datetime.now(timezone.utc).isoformat()
        }
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        subprocess.run(["git", "add", str(raw_path), str(adj_path), str(meta_path)], check=True)
        if subprocess.run(["git", "diff", "--cached", "--quiet"]).returncode != 0:
            subprocess.run(["git", "commit", "-m", "Checkpoint base-volume-1 history batch %03d" % batch_no], check=True)
            subprocess.run(["git", "push", "origin", "HEAD:" + CHECKPOINT_BRANCH], check=True)
            run_id = os.getenv("GITHUB_RUN_ID")
            if run_id:
                subprocess.run(["git", "push", "origin", "HEAD:parallel-market-history-basevol1-" + run_id], check=True)
        print("BASEVOL1 CHECKPOINT: batch=%d/%d success=%d failed=%d" %
              (batch_no+1, len(batches), len(results), len(batch_failures)), flush=True)
        summaries.extend(meta["instruments"])
        failures.extend(batch_failures)

    manifest = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "filter": "baseVol exactly 1; ordinary TSE/IFB shares and investment funds only",
        "source": "TSETMC MarketWatch instrument payload",
        "candidate_count": len(candidates), "succeeded": len(summaries), "failed": len(failures),
        "workers": WORKERS, "batch_size": BATCH_SIZE,
        "excluded_master_code_count": len(excluded), "skipped": skipped,
        "instruments": summaries, "failures": failures,
        "note": "This is a separate incremental archive. Merge/deduplicate by insCode before adding to the master universe."
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    subprocess.run(["git", "add", str(OUT / "manifest.json")], check=True)
    if subprocess.run(["git", "diff", "--cached", "--quiet"]).returncode != 0:
        subprocess.run(["git", "commit", "-m", "Finalize base-volume-1 history manifest"], check=True)
        subprocess.run(["git", "push", "origin", "HEAD:" + CHECKPOINT_BRANCH], check=True)
        run_id = os.getenv("GITHUB_RUN_ID")
        if run_id:
            subprocess.run(["git", "push", "origin", "HEAD:parallel-market-history-basevol1-" + run_id], check=True)
    print("BASEVOL1 COMPLETE: candidates=%d success=%d failed=%d" %
          (len(candidates), len(summaries), len(failures)), flush=True)

if __name__ == "__main__":
    main()
