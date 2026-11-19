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
    # Double tops/bottoms and head-and-shoulders from 5 alternating pivots.
    for j in range(4, len(pv)):
        a,b,c,d,e = pv[j-4:j+1]
        vals=[a[2],b[2],c[2],d[2],e[2]]
        dates=[rows[x[0]]["date"] for x in (a,b,c,d,e)]
        if [x[1] for x in (a,b,c,d,e)] == ["L","H","L","H","L"]:
            similarity=abs(a[2]-e[2])/((a[2]+e[2])/2)
            if similarity <= .06 and c[2] > max(a[2],e[2])*1.045:
                neck=(b[2]+d[2])/2
                status="تأیید شکست" if rows[-1]["close"] > neck else ("در حال تشکیل" if e[0] >= len(rows)-25 else "بدون شکست")
                found.append(dict(pattern="کف سه‌قلو / سر و شانه معکوس احتمالی", dates=dates, level=neck, status=status, end_index=e[0], quality=round((1-similarity)*100,1)))
            if similarity <= .035 and b[2] > max(a[2],e[2])*1.05:
                neck=(b[2]+d[2])/2
                status="تأیید شکست" if rows[-1]["close"] > neck else ("در حال تشکیل" if e[0] >= len(rows)-25 else "بدون شکست")
                found.append(dict(pattern="کف دوقلو", dates=[dates[0],dates[1],dates[2]], level=neck, status=status, end_index=c[0], quality=round((1-similarity)*100,1)))
        if [x[1] for x in (a,b,c,d,e)] == ["H","L","H","L","H"]:
            similarity=abs(a[2]-e[2])/((a[2]+e[2])/2)
            if similarity <= .06 and c[2] < min(a[2],e[2])*.955:
                neck=(b[2]+d[2])/2
                status="تأیید شکست نزولی" if rows[-1]["close"] < neck else ("در حال تشکیل" if e[0] >= len(rows)-25 else "بدون شکست")
                found.append(dict(pattern="سقف سه‌قلو / سر و شانه احتمالی", dates=dates, level=neck, status=status, end_index=e[0], quality=round((1-similarity)*100,1)))
            if similarity <= .035 and b[2] < min(a[2],e[2])*.95:
                neck=(b[2]+d[2])/2
                status="تأیید شکست نزولی" if rows[-1]["close"] < neck else ("در حال تشکیل" if e[0] >= len(rows)-25 else "بدون شکست")
                found.append(dict(pattern="سقف دوقلو", dates=[dates[0],dates[1],dates[2]], level=neck, status=status, end_index=c[0], quality=round((1-similarity)*100,1)))
    # Converging triangle: last 6 alternating pivots with descending highs and ascending lows.
    for j in range(5, len(pv)):
        q=pv[j-5:j+1]
        highs=[x for x in q if x[1]=="H"]; lows=[x for x in q if x[1]=="L"]
        if len(highs)==3 and len(lows)==3:
            hs=highs[-1][2]/highs[0][2]-1
            ls=lows[-1][2]/lows[0][2]-1
            if hs < -.025 and ls > .025 and highs[-1][0] > highs[0][0] and lows[-1][0] > lows[0][0]:
                upper=line_y(highs[0],highs[-1],len(rows)-1)
                lower=line_y(lows[0],lows[-1],len(rows)-1)
                if upper > lower:
                    status="نزدیک رأس؛ نیازمند شکست" if len(rows)-1 > q[-1][0] else "در حال تشکیل"
                    found.append(dict(pattern="مثلث متقارن", dates=[rows[q[0][0]]["date"],rows[q[-1][0]]["date"]], level=round((upper+lower)/2,2), status=status, end_index=q[-1][0], quality=round(min(abs(hs),abs(ls))*100,1)))
    # Deduplicate same pattern around same ending pivot.
    unique={}
    for x in found:
        k=(x["pattern"],x["end_index"])
        if k not in unique or x["quality"]>unique[k]["quality"]: unique[k]=x
    return sorted(unique.values(), key=lambda x:x["end_index"])

def main():
    rows=load_rows(); pv=pivots(rows); patterns=scan(rows,pv)
    last=rows[-1]; recent=[p for p in patterns if p["end_index"] >= len(rows)-180]
    # Forward returns after detected structural breakout; close-to-close, no look-ahead claim.
    for p in patterns:
        idx=p["end_index"]
        p["pattern_date"]=rows[idx]["date"]
        p["forward_returns_pct"]={}
        for n in (5,10,20):
            if idx+n < len(rows):
                p["forward_returns_pct"][str(n)]=round((rows[idx+n]["close"]/rows[idx]["close"]-1)*100,2)
        p.pop("end_index",None)
    OUT.mkdir(exist_ok=True)
    report={"symbol":SYMBOL,"source_branch":BRANCH,"source_file":SOURCE,"timeframe":"daily","price_series":"corporate-action adjusted close","rows":len(rows),"first_date":rows[0]["date"],"latest_date":last["date"],"latest_close":last["close"],"pivot_count":len(pv),"pattern_count":len(patterns),"recent_patterns":recent,"all_patterns":patterns[-100:]}
    (OUT/"sakht_patterns.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    md=[f"# گزارش الگوهای تکنیکال {SYMBOL}","",f"- داده: {len(rows)} جلسه، از {rows[0]['date']} تا {last['date']}","- آخرین قیمت پایانی تعدیل‌شده: "+f"{last['close']:,.2f}",f"- نقاط چرخش استخراج‌شده: {len(pv)}",f"- الگوهای تاریخی شناسایی‌شده: {len(patterns)}","","> این خروجی غربال الگوریتمی است؛ الگوها باید با نمودار و حجم معاملات بازبینی شوند. شکست فقط با بسته‌شدن قیمت تأیید می‌شود.","","## الگوهای نزدیک به وضعیت فعلی"]
    if not recent: md.append("در پنجره ۱۸۰ جلسه اخیر الگویی با معیارهای فعلی شناسایی نشد.")
    for p in recent[-15:]:
        md += [f"### {p['pattern']} — {p['pattern_date']}",f"- وضعیت: {p['status']}",f"- سطح مرجع: {p['level']:,.2f}",f"- کیفیت هندسی تقریبی: {p['quality']}%",f"- بازده بعد از ۵/۱۰/۲۰ جلسه: {p.get('forward_returns_pct',{})}",""]
    md += ["## آخرین الگوهای تاریخی"]
    for p in patterns[-30:]:
        md.append(f"- {p['pattern']} | {p['pattern_date']} | {p['status']} | سطح {p['level']:,.2f} | بازده‌ها {p.get('forward_returns_pct',{})}")
    (OUT/"sakht_patterns.md").write_text("\n".join(md),encoding="utf-8")
    print(json.dumps({"rows":len(rows),"first":rows[0]["date"],"latest":last["date"],"close":last["close"],"pivots":len(pv),"patterns":len(patterns),"recent":recent[-10:]},ensure_ascii=False))
if __name__=="__main__": main()
