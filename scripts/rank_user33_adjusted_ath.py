#!/usr/bin/env python3
import csv,gzip,json,glob,os
from pathlib import Path
root=Path("data/market_history_user33")
results=[]
for p in sorted(root.glob("*_adjusted.csv.gz")):
    rows=list(csv.DictReader(gzip.open(p,"rt",encoding="utf-8-sig")))
    if not rows: continue
    rows.sort(key=lambda r:r["date"])
    highs=[(float(r["high"]),r["date"]) for r in rows if float(r.get("high") or 0)>0]
    closes=[(float(r["close"]),r["date"]) for r in rows if float(r.get("close") or 0)>0]
    if not highs or not closes: continue
    ath,ath_date=max(highs)
    current,current_date=closes[-1]
    gap=(1-current/ath)*100
    results.append({"symbol":rows[-1]["symbol"],"latest_date":current_date,"current_adjusted_close":round(current,4),"adjusted_ath_high":round(ath,4),"ath_date":ath_date,"distance_from_ath_pct":round(gap,2),"rows":len(rows)})
results.sort(key=lambda x:x["distance_from_ath_pct"],reverse=True)
out={"basis":"latest adjusted close versus maximum adjusted daily high","symbols_count":len(results),"results":results}
(root/"adjusted_ath_distance_ranking.json").write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps(out,ensure_ascii=False,indent=2))
