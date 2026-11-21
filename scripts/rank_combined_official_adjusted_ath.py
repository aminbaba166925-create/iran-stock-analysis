#!/usr/bin/env python3
"""Re-rank the previously defined 445-share cohort from TSETMC's official adjusted chart."""
import concurrent.futures, json, time, urllib.request
from datetime import datetime
from pathlib import Path

BASE="https://members.tsetmc.com/tsev2/chart/data/Financial.aspx"
HEADERS={"User-Agent":"Mozilla/5.0","Referer":"https://www.tsetmc.com/","Accept":"text/plain,*/*"}
START="20180321"
LATEST_CUTOFF="20261110"
OUT=Path("reports"); OUT.mkdir(exist_ok=True)

def fetch_one(inst):
    code=str(inst["insCode"]); last=None
    for attempt in range(4):
        try:
            url=f"{BASE}?i={code}&t=ph&a=1"
            req=urllib.request.Request(url,headers=HEADERS)
            with urllib.request.urlopen(req,timeout=30) as r:
                body=r.read().decode("utf-8-sig","replace")
            rows=[]
            for line in body.strip().split(";"):
                cols=[x.strip() for x in line.split(",")]
                if len(cols)<7: continue
                date=cols[0]
                try: high=float(cols[1]); volume=float(cols[5]); close=float(cols[6])
                except (ValueError,TypeError): continue
                if len(date)!=8 or date<START or high<=0 or close<=0 or volume<=0: continue
                rows.append((date,high,close))
            rows.sort(key=lambda x:x[0])
            if len(rows)<200: raise RuntimeError(f"only {len(rows)} valid rows since 1397")
            latest=rows[-1]
            if latest[0]<LATEST_CUTOFF: raise RuntimeError(f"stale latest row {latest[0]}")
            peak=max(rows,key=lambda x:x[1])
            return {"insCode":code,"symbol":inst.get("symbol",""),"name":inst.get("name",""),
                    "rows":len(rows),"peak_adjusted_high":round(peak[1],4),"peak_date":peak[0],
                    "latest_adjusted_close":round(latest[2],4),"latest_date":latest[0],
                    "distance_pct":round((1-latest[2]/peak[1])*100,2),
                    "source":"TSETMC Financial.aspx official adjusted chart (a=1)"}
        except Exception as e:
            last=e; time.sleep(1.5*(attempt+1))
    return {"insCode":code,"symbol":inst.get("symbol",""),"success":False,"error":str(last)[:300]}

# The previous combined file is used only to preserve the exact cohort membership.
# Its previously calculated prices/ranks are discarded completely.
old=json.loads(Path("data/combined_adjusted_ath_ranking.json").read_text(encoding="utf-8"))
cohort=[]; seen=set()
for x in old.get("all_ranked",[]):
    code=str(x.get("insCode",""))
    if code and code not in seen:
        seen.add(code); cohort.append({"insCode":code,"symbol":x.get("symbol",""),"name":x.get("name","")})

# Independent control symbol supplied by the user; validated but not silently added to the cohort.
control={"insCode":"35964395659427029","symbol":"قشکر","name":"شکر"}
targets=cohort+[control]
results=[]
with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
    futures=[pool.submit(fetch_one,x) for x in targets]
    for i,f in enumerate(concurrent.futures.as_completed(futures),1):
        x=f.result(); results.append(x)
        print(f"OFFICIAL {i}/{len(targets)} {x.get('symbol')} {'OK' if 'distance_pct' in x else 'FAILED'}",flush=True)

by_code={x["insCode"]:x for x in results}
ranked=[by_code[x["insCode"]] for x in cohort if x["insCode"] in by_code and "distance_pct" in by_code[x["insCode"]]]
ranked.sort(key=lambda x:x["distance_pct"],reverse=True)
failed=[x for x in results if "distance_pct" not in x]
control_result=by_code.get(control["insCode"],{})
payload={"method":"(1 - latest official adjusted close / maximum official adjusted daily high since 2018-03-21) * 100",
"source":"TSETMC Financial.aspx chart API, parameter a=1 (official adjusted series)",
"cohort":"Exact 445-instrument all_ranked cohort from previous unified run; old prices/ranks discarded; 16 separate user symbols included in that cohort",
"period_start":"2018-03-21 (1397-01-01)","latest_cutoff":LATEST_CUTOFF,
"requested_cohort":len(cohort),"successful_cohort":len(ranked),
"failed_cohort":sum(1 for x in failed if x["insCode"] in seen),
"control_validation":control_result,"top50":ranked[:50],"all_ranked":ranked,
"failures":failed}
Path("reports/combined_official_adjusted_ath.json").write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding="utf-8")
lines=["# ۵۰ نماد با بیشترین فاصله از سقف تاریخی — تعدیل رسمی TSETMC","",
"مبنای قیمت: نمودار رسمی TSETMC با پارامتر a=1؛ قیمت‌های آرشیو تعدیل دستی قبلی در رتبه‌بندی استفاده نشده‌اند.",
"فرمول: (۱ − آخرین پایانی تعدیل‌شده ÷ بیشترین سقف روزانه تعدیل‌شده از ۱۳۹۷/۰۱/۰۱) × ۱۰۰.",
"جامعه آماری: همان مجموعه یکپارچه ۴۴۵ نمادی قبلی؛ ۱۶ نماد استخراج‌شده جداگانه نیز در آن حفظ شده‌اند.",
f"موفق: {len(ranked)} از {len(cohort)} | ناموفق: {sum(1 for x in failed if x['insCode'] in seen)} | تاریخ آخرین داده باید از {LATEST_CUTOFF} جدیدتر باشد.","",
"| رتبه | نماد | فاصله از سقف | پایانی تعدیل‌شده | سقف تعدیل‌شده | تاریخ سقف | آخرین تاریخ | روز معاملاتی |",
"|---:|---|---:|---:|---:|---|---|---:|"]
for i,x in enumerate(ranked[:50],1):
    lines.append(f"| {i} | {x['symbol']} | {x['distance_pct']:.2f}% | {x['latest_adjusted_close']:,.2f} | {x['peak_adjusted_high']:,.2f} | {x['peak_date']} | {x['latest_date']} | {x['rows']} |")
lines += ["","## کنترل مستقل قشکر",""]
if "distance_pct" in control_result:
    lines.append(f"قشکر: سقف {control_result['peak_adjusted_high']:,.2f} در {control_result['peak_date']}؛ آخرین پایانی {control_result['latest_adjusted_close']:,.2f} در {control_result['latest_date']}؛ فاصله {control_result['distance_pct']:.2f}%.")
else: lines.append("قشکر: دریافت داده ناموفق؛ " + control_result.get("error",""))
if failed:
    lines += ["","## داده‌های ناموفق",""]+[f"- {x['symbol']} ({x['insCode']}): {x.get('error','')}" for x in failed]
Path("reports/combined_official_adjusted_ath.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
print(json.dumps({"requested":len(cohort),"successful":len(ranked),"failed":sum(1 for x in failed if x["insCode"] in seen),"control":control_result,"top10":ranked[:10]},ensure_ascii=False))
