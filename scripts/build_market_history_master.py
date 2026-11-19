#!/usr/bin/env python3
"""Build a verified, de-duplicated index across durable TSE/IFB history checkpoints."""
from __future__ import annotations
import argparse
import csv
import gzip
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))

def inspect_batch(batch_dir: Path, branch: str, dataset: str):
    records, failures, batch_checks = [], [], []
    batches = sorted(batch_dir.glob("batch_[0-9][0-9][0-9].json"))
    for meta_path in batches:
        meta = read_json(meta_path)
        stem = meta_path.stem
        raw_path = batch_dir / (stem + "_raw.csv.gz")
        adj_path = batch_dir / (stem + "_adjusted.csv.gz")
        if not raw_path.is_file() or not adj_path.is_file():
            raise RuntimeError(f"Missing paired raw/adjusted archive for {branch}:{stem}")
        expected = {str(x["insCode"]): int(x["rows"]) for x in meta.get("instruments", [])}
        raw_counts, adj_counts = Counter(), Counter()
        raw_total = adj_total = 0
        for path, counts, kind in ((raw_path, raw_counts, "raw"), (adj_path, adj_counts, "adjusted")):
            with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
                reader = csv.DictReader(handle)
                required = {"insCode", "date", "symbol"}
                if not required.issubset(set(reader.fieldnames or [])):
                    raise RuntimeError(f"Unexpected {kind} schema in {path}")
                for row in reader:
                    code = str(row.get("insCode", "")).strip()
                    if code:
                        counts[code] += 1
                        if kind == "raw":
                            raw_total += 1
                        else:
                            adj_total += 1
        for code, expected_rows in expected.items():
            if raw_counts[code] != expected_rows or adj_counts[code] != expected_rows:
                raise RuntimeError(
                    f"Row mismatch {branch}:{stem}:{code}: expected={expected_rows}, "
                    f"raw={raw_counts[code]}, adjusted={adj_counts[code]}"
                )
        if raw_total != int(meta.get("raw_rows", raw_total)) or adj_total != raw_total:
            raise RuntimeError(
                f"Batch total mismatch {branch}:{stem}: meta={meta.get('raw_rows')}, "
                f"raw={raw_total}, adjusted={adj_total}"
            )
        batch_checks.append({
            "batch": int(meta.get("batch", int(stem.split("_")[-1]))),
            "success": int(meta.get("success", len(expected))),
            "failed": int(meta.get("failed", 0)),
            "raw_rows": raw_total,
            "adjusted_rows": adj_total,
            "corporate_action_events": int(meta.get("events", 0)),
            "saved_at_utc": meta.get("saved_at_utc", "")
        })
        for item in meta.get("instruments", []):
            records.append({
                "insCode": str(item["insCode"]),
                "symbol": str(item.get("symbol", "")).strip(),
                "asset_type": item.get("asset_type", ""),
                "rows": int(item.get("rows", 0)),
                "latest_date": str(item.get("latest_date", "")),
                "events": int(item.get("events", 0)),
                "branch": branch,
                "dataset": dataset,
                "batch": int(meta.get("batch", int(stem.split("_")[-1]))),
                "raw_file": f"{batch_dir.name}/{stem}_raw.csv.gz",
                "adjusted_file": f"{batch_dir.name}/{stem}_adjusted.csv.gz"
            })
        for failure in meta.get("failures", []):
            failures.append({**failure, "branch": branch, "batch": int(meta.get("batch", 0))})
    return records, failures, batch_checks

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--v2-dir", type=Path, required=True)
    ap.add_argument("--v3-dir", type=Path, required=True)
    ap.add_argument("--v4-dir", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    v2_records, v2_failures, v2_batches = inspect_batch(args.v2_dir, "market-history-checkpoints", "v2")
    v3_records, v3_failures, v3_batches = inspect_batch(args.v3_dir, "market-history-checkpoints-v3", "v3")
    v4_records, v4_failures, v4_batches = inspect_batch(args.v4_dir, "market-history-checkpoints-v4", "v4")
    all_records = v2_records + v3_records + v4_records
    grouped = defaultdict(list)
    for record in all_records:
        grouped[record["insCode"]].append(record)

    eligible, after_hours, duplicates = [], [], []
    for code, variants in grouped.items():
        normal = [x for x in variants if not x["symbol"].endswith("3")]
        candidates = normal if normal else variants
        candidates.sort(key=lambda x: (0 if x["dataset"] == "v2" else 1, x["batch"]))
        chosen = dict(candidates[0])
        chosen["duplicate_records_found"] = len(variants) - 1
        if normal:
            eligible.append(chosen)
        else:
            after_hours.append(chosen)
        if len(variants) > 1:
            duplicates.append({
                "insCode": code,
                "symbols": sorted(set(x["symbol"] for x in variants)),
                "occurrences": len(variants),
                "kept_source": f"{chosen['dataset']}/batch_{chosen['batch']:03d}"
            })

    eligible.sort(key=lambda x: x["symbol"])
    after_hours.sort(key=lambda x: x["symbol"])
    failures_by_code = {}
    for failure in v2_failures + v3_failures + v4_failures:
        code = str(failure.get("insCode", "")).strip()
        key = code or (failure.get("symbol", ""), failure.get("error", ""))
        failures_by_code.setdefault(key, failure)
    failures = list(failures_by_code.values())

    args.out.mkdir(parents=True, exist_ok=True)
    eligible_codes = sorted({x["insCode"] for x in eligible})
    (args.out / "eligible_codes.json").write_text(
        json.dumps({"insCodes": eligible_codes}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8"
    )
    (args.out / "after_hours_suffix3_excluded.json").write_text(
        json.dumps(after_hours, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (args.out / "duplicates.json").write_text(
        json.dumps(duplicates, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (args.out / "failures.json").write_text(
        json.dumps(failures, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (args.out / "index.json").write_text(
        json.dumps(eligible, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    all_success_records = len(all_records)
    unique_codes = len(grouped)
    raw_rows = sum(x["rows"] for x in eligible)
    events = sum(x["events"] for x in eligible)
    latest = max((x["latest_date"] for x in eligible), default="")
    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "sources": [
            {"branch": "market-history-checkpoints", "dataset": "v2", "batches": v2_batches},
            {"branch": "market-history-checkpoints-v3", "dataset": "v3", "batches": v3_batches},
            {"branch": "market-history-checkpoints-v4", "dataset": "v4", "batches": v4_batches}
        ],
        "source_batch_count": len(v2_batches) + len(v3_batches) + len(v4_batches),
        "successful_instrument_records_across_sources": all_success_records,
        "unique_insCodes_across_sources": unique_codes,
        "duplicate_instrument_records": all_success_records - unique_codes,
        "eligible_unique_instruments": len(eligible),
        "after_hours_suffix3_unique_excluded": len(after_hours),
        "eligible_raw_rows": raw_rows,
        "eligible_adjusted_rows_expected": raw_rows,
        "eligible_corporate_action_events": events,
        "failed_instrument_records": sum(int(x.get("failed", 0)) for x in v2_batches + v3_batches),
        "unique_failure_entries": len(failures),
        "latest_date": latest,
        "verification": {
            "all_batch_raw_and_adjusted_archives_exist": True,
            "per_instrument_raw_row_counts_match_metadata": True,
            "per_instrument_adjusted_row_counts_match_metadata": True,
            "batch_raw_totals_match_metadata": True,
            "suffix3_only_symbols_excluded_from_eligible_index": True
        },
        "important": "Historical archives remain in their original checkpoint branches. This master index references their exact source branch, batch and filenames; no source data was deleted or rewritten."
    }
    (args.out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (args.out / "README.md").write_text(
        "# Master market-history index\n\n"
        "This directory is a verified, de-duplicated index of history archives stored on the "
        "market-history-checkpoints, market-history-checkpoints-v3 and market-history-checkpoints-v4 branches.\n\n"
        "- index.json: eligible unique ordinary shares and funds, excluding suffix-3 after-hours instruments.\n"
        "- eligible_codes.json: successful instrument codes, suitable as the exclusion list for the next incremental fetch.\n"
        "- after_hours_suffix3_excluded.json: suffix-3-only instruments excluded from analysis.\n"
        "- duplicates.json: repeated instrument codes found across source batches/runs.\n"
        "- failures.json: failed instruments retained for retry.\n"
        "- summary.json: counts and archive integrity checks.\n\n"
        "Raw and adjusted daily archives remain unchanged in their original checkpoint branches.\n",
        encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)

if __name__ == "__main__":
    main()
