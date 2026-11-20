#!/usr/bin/env python3
"""Unified adjusted-ATH ranking: baseVol==1 universe plus the 16 separately extracted user symbols."""
import csv,gzip,io,json,subprocess
from pathlib import Path
ROOT=Path("data")
BASE=ROOT/"market_history_basevol1"
USER=ROOT/"market_history_user33"
MASTER=ROOT/"market_history_master"/"index.json"
UNIVERSE=BASE/"universe.json"
OUT=ROOT/"combined_adjusted_ath_ranking.json"
MIN_ROWS=200
LATEST_CUTOFF="20261110"

def git_bytes(ref,path):
    p=subprocess.run(["git","show",f"origin/{ref}:{path}"],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    if p.returncode:
        raise RuntimeError(f"git show failed for {ref}:{path}: {p.stderr.decode(errors='replace')[-300:]}")
    return p.stdout

def read_gz_bytes(blob):
    with gzip.GzipFile(fileobj=io.BytesIO(blob),mode="rb") as gz:
        return list(csv.DictReader(io.TextIOWrapper(gz,encoding="utf-8-sig",newline="")))

def merge_rows(stats, rows):
    for r in rows:
        code=(r.get("insCode") or "").strip()
        sym=(r.get("symbol") or "").strip()
        date=(r.get("date") or "").strip()
        try:
            high=float(r.get("high") or 0); close=float(r.get("close") or 0)
        except (ValueError,TypeError): continue
        if not code or not sym or len(date)!=8 or high<=0 or close<=0: continue
        x=stats.setdefault(code,{"insCode":code,"symbol":sym,"name":r.get("name",""),"asset_type":r.get("asset_type",""),"rows":0,"peak":0.0,"peak_date":"","latest_date":"","latest_close":0.0})
        x["rows"]+=1
        if high>x["peak"]: x["peak"],x["peak_date"]=high,date
        if date>=x["latest_date"]: x["latest_date"],x["latest_close"]=date,close

stats={}
sources={"basevol1_batches":0,"master_archive_files":0,"user_supplied_symbols":0}
# 656 additional baseVol==1 histories already checkpointed on this branch.
for fp in sorted(BASE.glob("batch_*_adjusted.csv.gz")):
    with gzip.open(fp,"rt",encoding="utf-8-sig",newline="") as f: merge_rows(stats,csv.DictReader(f))
    sources["basevol1_batches"]+=1

# Include all 410 baseVol==1 instruments previously covered by the master archive.
master=json.loads(MASTER.read_text(encoding="utf-8"))
universe=json.loads(UNIVERSE.read_text(encoding="utf-8"))
base_codes={str(x.get("insCode")) for x in universe.get("already_covered_by_master",[])}
master_by_file={}
for item in master:
    code=str(item.get("insCode",""))
    if code in base_codes and item.get("adjusted_file") and item.get("branch"):
        path=item["adjusted_file"]
        if not path.startswith("data/"): path="data/"+path
        key=(item["branch"],path)
        master_by_file.setdefault(key,set()).add(code)
for (branch,path),codes in master_by_file.items():
    rows=read_gz_bytes(git_bytes(branch,path))
    merge_rows(stats,[r for r in rows if str(r.get("insCode","")).strip() in codes])
    sources["master_archive_files"]+=1

# Add the 16 separately requested symbols, deduplicating by instrument code.
for fp in sorted(USER.glob("*_adjusted.csv.gz")):
    with gzip.open(fp,"rt",encoding="utf-8-sig",newline="") as f: merge_rows(stats,csv.DictReader(f))
    sources["user_supplied_symbols"]+=1

items=[]
for x in stats.values():
    if x["asset_type"]!="share" or x["rows"]<MIN_ROWS or x["latest_date"]<LATEST_CUTOFF or x["peak"]<=0 or x["latest_close"]<=1: continue
    x["distance_pct"]=round((1-x["latest_close"]/x["peak"])*100,2)
    x["peak"]=round(x["peak"],4); x["latest_close"]=round(x["latest_close"],4)
    items.append(x)
items.sort(key=lambda z:z["distance_pct"],reverse=True)
payload={"method":"(1 - latest adjusted close / maximum adjusted daily high) * 100",
"data_source":"adjusted daily OHLC histories; baseVol==1 universe (including previously master-covered instruments) plus separately requested user symbols",
"as_of":"2026-11-18","deduplication_key":"insCode","filters":"ordinary shares only; funds excluded; minimum 200 trading rows; latest data on or after 2026-11-10",
"basevol1_observed":universe.get("observed_live_count"),"basevol1_expected_user_reported":universe.get("expected_count_user_reported"),
"basevol1_master_covered":len(base_codes),"basevol1_new_candidates":universe.get("new_to_fetch_count"),
"separate_user_symbols_files":sources["user_supplied_symbols"],"unique_instruments_with_history":len(stats),
"eligible_shares_ranked":len(items),"sources":sources,"top50":items[:50],"all_ranked":items}
OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding="utf-8")
lines=["# رتبه‌بندی یکپارچه فاصله از سقف تاریخی تعدیل‌شده","",
"مجموعه واحد: نمادهای حجم مبنای ۱ به‌علاوه ۱۶ نماد استخراج‌شده جداگانه؛ حذف تکراری‌ها با شناسه ابزار (insCode).",
"فاصله = (۱ - آخرین پایانی تعدیل‌شده ÷ بیشترین سقف روزانه تعدیل‌شده) × ۱۰۰.",
"فقط سهام عادی؛ صندوق‌ها و سابقه کمتر از ۲۰۰ روز معاملاتی یا داده قدیمی‌تر از 2026-11-10 حذف شده‌اند.",
f"تاریخ داده: 2026-11-18 | تعداد سهام واجد شرایط: {len(items)}","",
"| رتبه | نماد | فاصله از سقف | آخرین پایانی تعدیل‌شده | سقف تعدیل‌شده | تاریخ سقف | آخرین تاریخ | تعداد روز |",
"|---:|---|---:|---:|---:|---|---|---:|"]
for i,x in enumerate(items,1):
    if i>50: break
    lines.append(f"| {i} | {x['symbol']} | {x['distance_pct']:.2f}% | {x['latest_close']:,.2f} | {x['peak']:,.2f} | {x['peak_date']} | {x['latest_date']} | {x['rows']} |")
(ROOT/"combined_adjusted_ath_ranking.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
print(json.dumps({"eligible_shares_ranked":len(items),"sources":sources,"top20":items[:20]},ensure_ascii=False))
