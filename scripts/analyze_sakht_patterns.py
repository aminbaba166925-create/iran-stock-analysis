#!/usr/bin/env python3
"""Technical chart-pattern scan for the adjusted daily history of ثاخت."""
import csv, gzip, io, json, math, subprocess
from datetime import datetime
from pathlib import Path

BRANCH = "market-history-checkpoints"
SOURCE = "data/market_history_parallel_v2/batch_002_adjusted.csv.gz"
OUT = Path("reports")
SYMBOL = "ثاخت"

def load_rows():
    blob = subprocess.check_output(["git", "show", f"origin/{BRANCH}:{SOURCE}"])
    rows = list(csv.DictReader(io.TextIOWrapper(gzip.GzipFile(fileobj=io.BytesIO(blob)), encoding="utf-8")))
    rows.sort(key=lambda r: r["date"])
    out = []
    for r in rows:
        if r.get("symbol") != SYMBOL:
            continue
        try:
            d = datetime.strptime(r["date"], "%Y%m%d").date()
            c, h, l, v = float(r["close"]), float(r["high"]), float(r["low"]), float(r["volume"])
            if c > 0: out.append({"date": d.isoformat(), "close": c, "high": h, "low": l, "volume": v})
        except (ValueError, KeyError): pass
    if len(out) < 100: raise RuntimeError(f"Not enough valid rows: {len(out)}")
    return out

def pivots(rows, radius=4, min_move=0.035):
    raw = []
    for i in range(radius, len(rows)-radius):
        c = rows[i]["close"]
        win = [x["close"] for x in rows[i-radius:i+radius+1]]
        if c == max(win) and win.count(c) == 1: raw.append((i, "H", c))
        elif c == min(win) and win.count(c) == 1: raw.append((i, "L", c))
    # Keep alternating extrema; if same type, retain the more extreme one.
    alt = []
    for p in raw:
        if alt and p[1] == alt[-1][1]:
            if (p[1] == "H" and p[2] > alt[-1][2]) or (p[1] == "L" and p[2] < alt[-1][2]): alt[-1] = p
        else:
            alt.append(p)
    # Filter tiny swings without losing the newest point.
    filt = []
    for p in alt:
        if not filt or abs(p[2]/filt[-1][2]-1) >= min_move: filt.append(p)
        elif (p[1] == "H" and p[2] > filt[-1][2]) or (p[1] == "L" and p[2] < filt[-1][2]): filt[-1] = p
    return filt

def line_y(p1, p2, x):
    return p1[2] + (p2[2]-p1[2])*(x-p1[0])/(p2[0]-p1[0])

def scan(rows, pv):
    found=[]
    def add(name, pts, neckline, direction, quality):
        end_i=pts[-1][0]
        breakout=None
        for k in range(end_i+1, min(len(rows), end_i+31)):
            close=rows[k]["close"]
            if (direction=="up" and close>neckline*1.01) or (direction=="down" and close<neckline*.99):
                breakout=k; break
        if breakout is not None:
            status="شکست صعودی تأییدشده" if direction=="up" else "شکست نزولی تأییدشده"
            base=breakout
        else:
            status="در حال تشکیل" if len(rows)-1-end_i<=20 else "بدون شکست در ۳۰ جلسه بعد"
            base=end_i
        rets={}
        if breakout is not None:
            for n in (5,10,20):
                if breakout+n<len(rows):
                    rets[str(n)]=round((rows[breakout+n]["close"]/rows[breakout]["close"]-1)*100,2)
        found.append({"pattern":name,"dates":[rows[p[0]]["date"] for p in pts],
          "pivot_prices":[round(p[2],2) for p in pts],"level":round(neckline,2),
          "status":status,"quality":round(quality,1),"formation_date":rows[end_i]["date"],
          "breakout_date":rows[breakout]["date"] if breakout is not None else None,
          "forward_returns_pct":rets,"end_index":end_i,"base_index":base})
    for j in range(2,len(pv)):
        a,b,c=pv[j-2:j+1]
        if [x[1] for x in (a,b,c)]==["H","L","H"]:
            sim=abs(a[2]-c[2])/((a[2]+c[2])/2)
            if sim<=.04 and 1-b[2]/min(a[2],c[2])>=.05:
                add("سقف دوقلو",[a,b,c],b[2],"down",(1-sim)*100)
        if [x[1] for x in (a,b,c)]==["L","H","L"]:
            sim=abs(a[2]-c[2])/((a[2]+c[2])/2)
            if sim<=.04 and b[2]/max(a[2],c[2])-1>=.05:
                add("کف دوقلو",[a,b,c],b[2],"up",(1-sim)*100)
    for j in range(4,len(pv)):
        a,b,c,d,e=pv[j-4:j+1]
        if [x[1] for x in (a,b,c,d,e)]==["H","L","H","L","H"]:
            sim=abs(a[2]-e[2])/((a[2]+e[2])/2)
            if sim<=.08 and c[2]>=max(a[2],e[2])*1.03:
                add("سر و شانه",[a,b,c,d,e],(b[2]+d[2])/2,"down",(1-sim)*100)
        if [x[1] for x in (a,b,c,d,e)]==["L","H","L","H","L"]:
            sim=abs(a[2]-e[2])/((a[2]+e[2])/2)
            if sim<=.08 and c[2]<=min(a[2],e[2])*.97:
                add("سر و شانه معکوس",[a,b,c,d,e],(b[2]+d[2])/2,"up",(1-sim)*100)
    for j in range(5,len(pv)):
        q=pv[j-5:j+1]; highs=[x for x in q if x[1]=="H"]; lows=[x for x in q if x[1]=="L"]
        if len(highs)==3 and len(lows)==3:
            hs=highs[-1][2]/highs[0][2]-1; ls=lows[-1][2]/lows[0][2]-1
            if hs<-.025 and ls>.025:
                upper=line_y(highs[0],highs[-1],len(rows)-1); lower=line_y(lows[0],lows[-1],len(rows)-1)
                if upper>lower:
                    found.append({"pattern":"مثلث متقارن","dates":[rows[q[0][0]]["date"],rows[q[-1][0]]["date"]],
                      "pivot_prices":[round(x[2],2) for x in q],"level":round((upper+lower)/2,2),
                      "status":"در حال فشردگی؛ منتظر شکست","quality":round(min(abs(hs),abs(ls))*100,1),
                      "formation_date":rows[q[-1][0]]["date"],"breakout_date":None,"forward_returns_pct":{},
                      "end_index":q[-1][0],"base_index":q[-1][0]})
    unique={}
    for x in found:
        key=(x["pattern"],x["end_index"])
        if key not in unique or x["quality"]>unique[key]["quality"]: unique[key]=x
    return sorted(unique.values(),key=lambda x:x["end_index"])

def main():
    rows=load_rows(); pv=pivots(rows); patterns=scan(rows,pv)
    last=rows[-1]; recent=[p for p in patterns if p["end_index"] >= len(rows)-180]
    # Forward returns after detected structural breakout; close-to-close, no look-ahead claim.
    for p in patterns:
        p.pop("end_index",None); p.pop("base_index",None)
    OUT.mkdir(exist_ok=True)
    report={"symbol":SYMBOL,"source_branch":BRANCH,"source_file":SOURCE,"timeframe":"daily","price_series":"corporate-action adjusted close","rows":len(rows),"first_date":rows[0]["date"],"latest_date":last["date"],"latest_close":last["close"],"pivot_count":len(pv),"pattern_count":len(patterns),"recent_patterns":recent,"all_patterns":patterns[-100:]}
    (OUT/"sakht_patterns.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    md=[f"# گزارش الگوهای تکنیکال {SYMBOL}","",f"- داده: {len(rows)} جلسه، از {rows[0]['date']} تا {last['date']}","- آخرین قیمت پایانی تعدیل‌شده: "+f"{last['close']:,.2f}",f"- نقاط چرخش استخراج‌شده: {len(pv)}",f"- الگوهای تاریخی شناسایی‌شده: {len(patterns)}","","> این خروجی غربال الگوریتمی است؛ الگوها باید با نمودار و حجم معاملات بازبینی شوند. شکست فقط با بسته‌شدن قیمت تأیید می‌شود.","","## الگوهای نزدیک به وضعیت فعلی"]
    if not recent: md.append("در پنجره ۱۸۰ جلسه اخیر الگویی با معیارهای فعلی شناسایی نشد.")
    for p in recent[-15:]:
        md += [f"### {p['pattern']} — {p['formation_date']}",f"- وضعیت: {p['status']}",f"- سطح مرجع: {p['level']:,.2f}",f"- کیفیت هندسی تقریبی: {p['quality']}%",f"- بازده بعد از ۵/۱۰/۲۰ جلسه: {p.get('forward_returns_pct',{})}",""]
    md += ["## آخرین الگوهای تاریخی"]
    for p in patterns[-30:]:
        md.append(f"- {p['pattern']} | {p['pattern_date']} | {p['status']} | سطح {p['level']:,.2f} | بازده‌ها {p.get('forward_returns_pct',{})}")
    (OUT/"sakht_patterns.md").write_text("\n".join(md),encoding="utf-8")
    print(json.dumps({"rows":len(rows),"first":rows[0]["date"],"latest":last["date"],"close":last["close"],"pivots":len(pv),"patterns":len(patterns),"recent":recent[-10:]},ensure_ascii=False))
if __name__=="__main__": main()
