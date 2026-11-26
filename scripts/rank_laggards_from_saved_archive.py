#!/usr/bin/env python3
"""Rank saved adjusted-history archives; do not call TSETMC."""
import csv, gzip, io, json, subprocess
from collections import defaultdict
from pathlib import Path

ROOT = Path(".")
INDEX = ROOT / "data/market_history_master/index.json"
OUT = ROOT / "reports"
START = "20260519"
EXCLUDED = {"آرمان","ثنام","خصدرا","خعمرا","داراب","شصفها","شگستر","فسا","گشان","مرقام","وملت"}
MIN_SESSIONS = 20

def git_bytes(branch, path):
    p = subprocess.run(["git","show",f"origin/{branch}:{'data/'+path}"],capture_output=True)
    if p.returncode:
        raise RuntimeError(p.stderr.decode("utf-8","replace")[:300])
    return p.stdout

def read_batch(branch, path):
    raw = git_bytes(branch, path)
    with gzip.GzipFile(fileobj=io.BytesIO(raw), mode="rb") as gz:
        text = io.TextIOWrapper(gz, encoding="utf-8-sig", newline="")
        reader = csv.DictReader(text)
        required = {"insCode","date","close","volume","symbol"}
        if not required.issubset(set(reader.fieldnames or [])):
            raise RuntimeError(f"Unexpected adjusted CSV columns: {reader.fieldnames}")
        by_code = defaultdict(list)
        for row in reader:
            d = str(row.get("date","")).strip()
            if d >= START:
                try:
                    close = float(row.get("close") or 0)
                    volume = float(row.get("volume") or 0)
                except (TypeError,ValueError):
                    continue
                if len(d)==8 and close>0 and volume>0:
                    by_code[str(row.get("insCode","")).strip()].append((d,close,volume))
        return by_code


def archived_metadata(branch, directory):
    listing = subprocess.run(["git","ls-tree","-r","--name-only","origin/"+branch,"data/"+directory],capture_output=True,text=True,check=True).stdout.splitlines()
    out=[]
    for path in listing:
        if not path.endswith(".json") or path.endswith("manifest.json") or path.endswith("universe.json"):
            continue
        raw=subprocess.run(["git","show","origin/"+branch+":"+path],capture_output=True,check=True).stdout
        try: meta=json.loads(raw)
        except Exception: continue
        for item in meta.get("instruments",[]):
            filename=path.rsplit("/",1)[1].replace(".json","_adjusted.csv.gz")
            out.append({"insCode":str(item.get("insCode","")),"symbol":item.get("symbol",""),
              "asset_type":item.get("asset_type",""),"branch":branch,
              "adjusted_file":path.removeprefix("data/").rsplit("/",1)[0]+"/"+filename})
    return out

def main():
    records = json.loads(INDEX.read_text(encoding="utf-8"))
    records += archived_metadata("market-history-basevol1-cohort-checkpoints","market_history_basevol1")
    records += archived_metadata("market-history-basevol-gt1-checkpoints","market_history_basevol_gt1")
    recovery_branch="market-history-user33-checkpoints"
    manifest_path="data/market_history_recovery_v2/recovery_v2_manifest.json"
    try:
        manifest=json.loads(subprocess.run(["git","show",recovery_branch+":"+manifest_path],capture_output=True,check=True).stdout)
        for item in manifest.get("results",[]):
            if item.get("success") and item.get("status")=="complete":
                sym=item.get("symbol","")
                ordinal=int(item.get("ordinal",0))
                filename="{:02d}_{}_adjusted.csv.gz".format(ordinal,sym)
                records.append({"insCode":str(item.get("insCode","")),"symbol":sym,
                  "asset_type":item.get("asset_type","share"),"branch":recovery_branch,
                  "adjusted_file":"market_history_recovery_v2/"+filename})
    except Exception:
        pass
    eligible = {}
    for x in records:
        code = str(x.get("insCode","")).strip()
        sym = str(x.get("symbol","")).strip()
        if code and x.get("asset_type")=="share" and sym not in EXCLUDED and not sym.endswith("3"):
            if code not in eligible or x.get("dataset") in ("v2","v3","v4"):
                eligible[code]=x

    grouped = defaultdict(list)
    for code,x in eligible.items():
        grouped[(x["branch"],x["adjusted_file"])].append(code)

    results, failures = [], []
    for (branch,path), codes in grouped.items():
        try:
            rows = read_batch(branch,path)
            for code in codes:
                x=eligible[code]
                series=sorted(rows.get(code,[]),key=lambda z:z[0])
                if len(series)<MIN_SESSIONS:
                    failures.append({"symbol":x["symbol"],"insCode":code,"reason":f"only {len(series)} valid sessions"})
                    continue
                first,last=series[0],series[-1]
                ret=(last[1]/first[1]-1)*100
                results.append({
                    "symbol":x["symbol"],"insCode":code,"baseline_date":first[0],
                    "baseline_adjusted_close":round(first[1],4),"latest_date":last[0],
                    "latest_adjusted_close":round(last[1],4),"adjusted_return_pct":round(ret,2),
                    "sessions":len(series),"archive_branch":branch,"archive_file":path
                })
        except Exception as e:
            for code in codes:
                failures.append({"symbol":eligible[code]["symbol"],"insCode":code,"reason":str(e)[:300]})

    results.sort(key=lambda x:x["adjusted_return_pct"])
    returns=sorted(x["adjusted_return_pct"] for x in results)
    median=returns[len(returns)//2] if returns else None
    payload={
      "title":"Saved adjusted-price laggard screen since 1405 reopening",
      "method":"(latest adjusted close / first adjusted close on or after 2026-05-19 - 1) * 100",
      "source":"Existing gzip CSV adjusted-history archives indexed by data/market_history_master/index.json; no TSETMC refetch",
      "start_date":"2026-05-19","start_date_jalali":"1405-02-29",
      "excluded_symbols":sorted(EXCLUDED),"eligible_shares":len(eligible),
      "included_sources":["master v2/v3/v4","basevol1 cohort","basevol>1 cohort","complete recovery histories"],
      "successful":len(results),"failed_or_insufficient":len(failures),
      "latest_data_date":max((x["latest_date"] for x in results),default=None),
      "median_adjusted_return_pct":median,"top50_laggards":results[:50],
      "all_ranked":results,"failures":failures
    }
    OUT.mkdir(exist_ok=True)
    (OUT/"laggards_since_reopening_1405_from_saved_archive.json").write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    lines=[
      "# ۵۰ نماد کم‌بازده از شروع بازار در سال ۱۴۰۵ — بر اساس آرشیو ذخیره‌شده","",
      "مبنای شروع: ۲۹ اردیبهشت ۱۴۰۵ (2026-05-19).",
      "روش: بازده قیمت پایانی تعدیل‌شده از اولین جلسه معتبر در/پس از تاریخ شروع تا آخرین جلسه ذخیره‌شده.",
      "منبع: فایل‌های CSV فشرده تعدیل‌شده موجود در شاخه‌های آرشیو پروژه؛ هیچ دریافت مجددی از TSETMC انجام نشده است.",
      "۱۱ نماد درخواستی حذف شده‌اند: "+"، ".join(sorted(EXCLUDED)),
      f"سهام واجد شرایط: {len(eligible)} | محاسبه موفق: {len(results)} | ناکافی/ناموفق: {len(failures)} | میانه بازده: {median}%.","",
      "| رتبه | نماد | بازده تعدیل‌شده | قیمت مبنا | آخرین قیمت تعدیل‌شده | تاریخ مبنا | آخرین تاریخ | جلسات |",
      "|---:|---|---:|---:|---:|---|---|---:|"
    ]
    for i,x in enumerate(results[:50],1):
      lines.append(f"| {i} | {x['symbol']} | {x['adjusted_return_pct']:.2f}% | {x['baseline_adjusted_close']:,.2f} | {x['latest_adjusted_close']:,.2f} | {x['baseline_date']} | {x['latest_date']} | {x['sessions']} |")
    lines += ["","## موارد ناکافی/ناموفق",""]+[f"- {x['symbol']}: {x['reason']}" for x in failures]
    (OUT/"laggards_since_reopening_1405_from_saved_archive.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
    print(json.dumps({"eligible":len(eligible),"successful":len(results),"failed":len(failures),"median":median,"top20":results[:20]},ensure_ascii=False))

if __name__=="__main__": main()
