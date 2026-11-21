#!/usr/bin/env python3
"""Rank baseVol==1 shares by distance from their official TSETMC adjusted chart ATH."""
import concurrent.futures, csv, json, time, urllib.request
from pathlib import Path

MANIFEST=Path("data/market_history_basevol1/manifest.json")
OUT=Path("reports")
OUT.mkdir(exist_ok=True)
BASE="https://members.tsetmc.com/tsev2/chart/data/Financial.aspx"
HEADERS={"User-Agent":"Mozilla/5.0","Referer":"https://www.tsetmc.com/","Accept":"text/plain,*/*"}

def fetch_one(inst):
    code=str(inst["insCode"])
    url=f"{BASE}?i={code}&t=ph&a=1"
    last=None
    for attempt in range(4):
        try:
            req=urllib.request.Request(url,headers=HEADERS)
            with urllib.request.urlopen(req,timeout=25) as r:
                body=r.read().decode("utf-8-sig","replace")
            records=[]
            for line in body.strip().split(";"):
                line=line.strip()
                if not line: continue
                cols=[x.strip() for x in line.split(",")]
                if len(cols)<7: continue
                date=cols[0]
                try:
                    high=float(cols[1]); volume=float(cols[5]); close=float(cols[6])
                except (ValueError,TypeError):
                    continue
                if len(date)!=8 or high<=0 or close<=0 or volume<=0: continue
                records.append((date,high,close,volume))
            if len(records)<30:
                raise RuntimeError(f"official adjusted chart returned only {len(records)} valid trading rows")
            records.sort(key=lambda x:x[0])
            peak=max(records,key=lambda x:x[1])
            latest=records[-1]
            dist=(1-latest[2]/peak[1])*100
            return {"insCode":code,"symbol":inst.get("symbol",""),"name":inst.get("name",""),
                    "rows":len(records),"peak_adjusted_high":round(peak[1],4),"peak_date":peak[0],
                    "latest_adjusted_close":round(latest[2],4),"latest_date":latest[0],
                    "distance_pct":round(dist,2),"source":"TSETMC official chart API a=1"}
        except Exception as e:
            last=e
            time.sleep(1.5*(attempt+1))
    return {"insCode":code,"symbol":inst.get("symbol",""),"success":False,"error":str(last)[:300]}

m=json.loads(MANIFEST.read_text(encoding="utf-8"))
instruments=[x for x in m.get("instruments",[]) if x.get("asset_type")=="share"]
results=[]
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
    futures=[pool.submit(fetch_one,x) for x in instruments]
    for i,f in enumerate(concurrent.futures.as_completed(futures),1):
        x=f.result()
        results.append(x)
        print(f"CHART {i}/{len(futures)} {x.get('symbol')} {'OK' if 'distance_pct' in x else 'FAILED'}",flush=True)
ok=[x for x in results if "distance_pct" in x]
failed=[x for x in results if "distance_pct" not in x]
ok.sort(key=lambda x:x["distance_pct"],reverse=True)
payload={"method":"(1 - latest adjusted close / maximum adjusted daily high) * 100",
         "data_source":"TSETMC official Financial.aspx chart API with a=1 (adjusted)",
         "filters":"ordinary shares only; trading rows with volume > 0; minimum 30 valid rows",
         "archive_cohort":"baseVol exactly 1","requested_shares":len(instruments),
         "succeeded":len(ok),"failed":len(failed),"top50":ok[:50],"failures":failed}
(OUT/"basevol1_official_adjusted_ath.json").write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding="utf-8")
lines=["# 50 نماد با بیشترین فاصله از سقف تاریخی — نمودار رسمی تعدیل‌شده TSETMC","",
"منبع: Financial.aspx با پارامتر a=1 (تعدیل‌شده رسمی). فقط سهام عادی؛ روزهای بدون معامله حذف شده‌اند؛ حداقل ۳۰ روز معاملاتی معتبر.",
"فاصله = (۱ - آخرین پایانی تعدیل‌شده ÷ بیشترین سقف روزانه تعدیل‌شده) × ۱۰۰.","",
f"تعداد نمادهای سهامی بررسی‌شده: {len(instruments)} | موفق: {len(ok)} | ناموفق: {len(failed)}","",
"| رتبه | نماد | فاصله از سقف | آخرین پایانی تعدیل‌شده | بیشترین سقف تعدیل‌شده | تاریخ سقف | آخرین تاریخ | تعداد روز |",
"|---:|---|---:|---:|---:|---|---|---:|"]
for i,x in enumerate(ok[:50],1):
    lines.append(f"| {i} | {x['symbol']} | {x['distance_pct']:.2f}% | {x['latest_adjusted_close']:,.2f} | {x['peak_adjusted_high']:,.2f} | {x['peak_date']} | {x['latest_date']} | {x['rows']} |")
if failed:
    lines += ["","## نمادهای ناموفق",""]+[f"- {x['symbol']}: {x.get('error','')}" for x in failed]
(OUT/"basevol1_official_adjusted_ath.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
print(json.dumps({"requested":len(instruments),"succeeded":len(ok),"failed":len(failed),"top10":ok[:10]},ensure_ascii=False))
