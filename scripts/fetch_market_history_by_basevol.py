#!/usr/bin/env python3
"""Collect daily history in two independent base-volume cohorts."""
from __future__ import annotations
import csv, gzip, json, os, subprocess, sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "scripts")
import scan_market
import fetch_market_history_next100_v4 as history

MODE = os.environ["BASEVOL_MODE"]          # eq1 or gt1
EXPECTED = int(os.getenv("EXPECTED_COUNT", "0"))
LABEL = "basevol1" if MODE == "eq1" else "basevol_gt1"
OUT = Path("data") / ("market_history_" + LABEL)
MASTER = Path("data/market_history_master/eligible_codes.json")
CHECKPOINT_BRANCH = os.getenv("CHECKPOINT_BRANCH", "market-history-" + LABEL + "-checkpoints")
BATCH_SIZE = 10
WORKERS = 4

def git_checkpoint(paths, message):
    subprocess.run(["git", "add", *[str(p) for p in paths]], check=True)
    if subprocess.run(["git", "diff", "--cached", "--quiet"]).returncode != 0:
        subprocess.run(["git", "commit", "-m", message], check=True)
        subprocess.run(["git", "push", "origin", "HEAD:" + CHECKPOINT_BRANCH], check=True)
        run_id = os.getenv("GITHUB_RUN_ID")
        if run_id:
            subprocess.run(["git", "push", "origin", "HEAD:parallel-market-history-" + LABEL + "-" + run_id], check=True)

def main():
    if MODE not in ("eq1", "gt1"):
        raise RuntimeError("BASEVOL_MODE must be eq1 or gt1")
    if not MASTER.exists():
        raise RuntimeError("Master index missing; refusing to run without deduplication")
    master = json.loads(MASTER.read_text(encoding="utf-8"))
    if not isinstance(master, dict) or not isinstance(master.get("insCodes"), list) or len(master["insCodes"]) < 100:
        raise RuntimeError("Invalid master eligible_codes.json")
    archived = {str(x).strip() for x in master["insCodes"] if str(x).strip()}

    params = ["market=0"] + [f"paperTypes[{i}]={i+1}" for i in range(9)]
    params += ["withBestLimits=false", "hEven=0", "RefID=0"]
    payload = scan_market.get_json(scan_market.BASE + "/ClosingPrice/GetMarketWatch?" + "&".join(params))
    rows = scan_market.find_list(payload, ("marketwatch", "marketWatch", "closingPriceInfo"))
    if not rows:
        raise RuntimeError("MarketWatch returned no instrument rows")

    candidates, already_archived, seen = [], [], set()
    skipped = {"suffix3": 0, "out_of_scope": 0, "missing_code": 0, "missing_basevol": 0, "wrong_basevol": 0}
    basevol_histogram = {}
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
        if not code or code == "0":
            skipped["missing_code"] += 1
            continue
        if code in seen:
            continue
        seen.add(code)
        if symbol.endswith("3"):
            skipped["suffix3"] += 1
            continue
        asset_type = scan_market.classify_instrument(source, item, symbol, name)
        if asset_type not in ("share", "fund"):
            skipped["out_of_scope"] += 1
            continue
        base_vol = scan_market.num(source, "baseVol", "baseVolume")
        if not base_vol:
            base_vol = scan_market.num(item, "baseVol", "baseVolume")
        if base_vol <= 0:
            skipped["missing_basevol"] += 1
            continue
        key = str(int(base_vol)) if float(base_vol).is_integer() else str(base_vol)
        basevol_histogram[key] = basevol_histogram.get(key, 0) + 1
        matches = (base_vol == 1) if MODE == "eq1" else (base_vol > 1)
        if not matches:
            skipped["wrong_basevol"] += 1
            continue
        row = {"symbol": symbol, "name": name, "asset_type": asset_type,
               "insCode": code, "baseVol": base_vol}
        if code in archived:
            already_archived.append(row)
        else:
            candidates.append(row)

    candidates.sort(key=lambda x: (x["asset_type"], x["symbol"]))
    already_archived.sort(key=lambda x: (x["asset_type"], x["symbol"]))
    universe_count = len(candidates) + len(already_archived)
    print(f"COHORT={LABEL} live_universe={universe_count} new_to_fetch={len(candidates)} already_in_master={len(already_archived)} expected={EXPECTED} histogram={json.dumps(basevol_histogram, ensure_ascii=False)} skipped={json.dumps(skipped, ensure_ascii=False)}", flush=True)
    if EXPECTED and universe_count != EXPECTED:
        print(f"WARNING: observed cohort count {universe_count} differs from user-reported {EXPECTED}; preserving observed data and flagging for review.", flush=True)
    if not universe_count:
        raise RuntimeError("No instruments matched cohort; check live baseVol field mapping")

    OUT.mkdir(parents=True, exist_ok=True)
    universe = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "filter": "baseVol == 1" if MODE == "eq1" else "baseVol > 1",
        "expected_count_user_reported": EXPECTED,
        "observed_live_count": universe_count,
        "new_to_fetch_count": len(candidates),
        "already_covered_by_master_count": len(already_archived),
        "already_covered_by_master": already_archived,
        "candidates": candidates,
        "base_volume_histogram": basevol_histogram,
        "skipped": skipped
    }
    universe_path = OUT / "universe.json"
    universe_path.write_text(json.dumps(universe, ensure_ascii=False, indent=2), encoding="utf-8")
    git_checkpoint([universe_path], "Save " + LABEL + " verified universe")

    summaries, failures = [], []
    batches = [candidates[i:i+BATCH_SIZE] for i in range(0, len(candidates), BATCH_SIZE)]
    fields = history.FIELDS
    for batch_no, batch in enumerate(batches):
        raw_path = OUT / f"batch_{batch_no:03d}_raw.csv.gz"
        adj_path = OUT / f"batch_{batch_no:03d}_adjusted.csv.gz"
        meta_path = OUT / f"batch_{batch_no:03d}.json"
        if meta_path.exists():
            try:
                old = json.loads(meta_path.read_text(encoding="utf-8"))
                done_codes = {str(x.get("insCode")) for x in old.get("instruments", [])}
                failed_codes = {str(x.get("insCode")) for x in old.get("failures", [])}
                batch_codes = {x["insCode"] for x in batch}
                if batch_codes.issubset(done_codes) and not (batch_codes & failed_codes):
                    summaries.extend(old.get("instruments", []))
                    print(f"RESUME: batch {batch_no} already complete", flush=True)
                    continue
            except Exception:
                pass

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
                    batch_failures.append({"insCode": item["insCode"], "symbol": item["symbol"], "error": str(exc)[:400]})
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
            "failures": batch_failures,
            "saved_at_utc": datetime.now(timezone.utc).isoformat()
        }
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        git_checkpoint([raw_path, adj_path, meta_path], f"Checkpoint {LABEL} batch {batch_no:03d}")
        summaries.extend(meta["instruments"])
        failures.extend(batch_failures)
        print(f"CHECKPOINT {LABEL}: batch={batch_no+1}/{len(batches)} success={len(results)} failed={len(batch_failures)}", flush=True)

    manifest = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "cohort": LABEL, "filter": "baseVol == 1" if MODE == "eq1" else "baseVol > 1",
        "observed_live_universe": universe_count, "expected_count_user_reported": EXPECTED,
        "already_covered_by_master": len(already_archived),
        "new_candidates": len(candidates), "succeeded": len(summaries), "failed": len(failures),
        "raw_rows": sum(x.get("rows", 0) for x in summaries),
        "corporate_action_events": sum(x.get("events", 0) for x in summaries),
        "workers": WORKERS, "batch_size": BATCH_SIZE,
        "failures": failures,
        "note": "Master-covered instruments are listed in universe.json and not downloaded again; all new data is saved separately pending master deduplication."
    }
    manifest_path = OUT / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    git_checkpoint([manifest_path], "Finalize " + LABEL + " history manifest")
    print("COMPLETE " + json.dumps(manifest, ensure_ascii=False), flush=True)

if __name__ == "__main__":
    main()
