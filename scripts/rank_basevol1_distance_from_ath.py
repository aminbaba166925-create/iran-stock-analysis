#!/usr/bin/env python3
import csv, gzip, json
from pathlib import Path
from collections import defaultdict

ROOT=Path("data/market_history_basevol1")
OUT=Path("reports")
OUT.mkdir(exist_ok=True)
stats={}
files=sorted(ROOT.glob("batch_*_adjusted.csv.gz"))
for fp in files:
    with gzip.open(fp,"rt",encoding="utf-8",newline="") as f:
        for r in csv.DictReader(f):
            code=(r.get("insCode") or "").strip()
            sym=(r.get("symbol") or "").strip()
            date=(r.get("date") or "").strip()
            try:
                high=float(r.get("high") or 0)
                close=float(r.get("close") or 0)
            except (ValueError,TypeError):
                continue
            if not code or not sym or not date or high<=0 or close<=0:
                continue
            x=stats.setdefault(code,{"insCode":code,"symbol":sym,"name":r.get("name",""),"asset_type":r.get("asset_type",""),"rows":0,"peak":0.0,"peak_date":"","latest_date":"","latest_close":0.0})
            x["rows"]+=1
            if high>x["peak"]:
                x["peak"]=high
                x["peak_date"]=date
            if date>=x["latest_date"]:
                x["latest_date"]=date
                x["latest_close"]=close

items=[]
for x in stats.values():
    # Rank ordinary listed shares only; exclude funds and thin/stale series.
    if x["asset_type"] != "share": continue
    if x["rows"] < 200 or x["latest_date"] < "20261110": continue
    if x["peak"]<=0 or x["latest_close"]<=1: continue
    x["distance_pct"]=(1-x["latest_close"]/x["peak"])*100
    x["peak"]=round(x["peak"],4)
    x["latest_close"]=round(x["latest_close"],4)
    x["distance_pct"]=round(x["distance_pct"],2)
    items.append(x)
items.sort(key=lambda z:z["distance_pct"],reverse=True)
top=items[:50]
payload={"method":"(1 - latest adjusted close / maximum adjusted daily high) * 100","source":"adjusted daily OHLC archive for baseVol==1 ordinary shares; funds, series under 200 rows, stale records excluded","archive_as_of":"2026-11-18","symbols_with_valid_data":len(items),"top50":top}
(OUT/"basevol1_distance_from_ath.json").write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding="utf-8")
lines=["# 50 نماد با بیشترین فاصله از سقف تاریخی تعدیل‌شده","", "مبنای محاسبه: (۱ - آخرین قیمت پایانی تعدیل‌شده ÷ بیشترین سقف روزانه تعدیل‌شده) × ۱۰۰","", "فقط سهام عادی؛ صندوق‌ها، سابقه کمتر از ۲۰۰ روز و داده‌های غیر به‌روز حذف شده‌اند. داده‌ها تا 2026-11-18. این رتبه‌بندی به‌تنهایی سیگنال خرید نیست.","", "| رتبه | نماد | فاصله از سقف | آخرین قیمت تعدیل‌شده | سقف تعدیل‌شده | تاریخ سقف | آخرین تاریخ | تعداد داده |","|---:|---|---:|---:|---:|---|---|---:|"]
for i,x in enumerate(top,1):
    lines.append(f"| {i} | {x['symbol']} | {x['distance_pct']:.2f}% | {x['latest_close']:,.2f} | {x['peak']:,.2f} | {x['peak_date']} | {x['latest_date']} | {x['rows']} |")
(OUT/"basevol1_distance_from_ath.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
print(json.dumps({"symbols":len(items),"top50":top},ensure_ascii=False))
