#!/usr/bin/env python3
"""Fetch the 33 user-specified base-volume>1 symbols one at a time."""
import csv, gzip, json, os, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "scripts")
import scan_market
import fetch_market_history_next100_v4 as history

SYMBOLS = [
 "آرمان","بایکا","پردیس","زندگی","بکهنوج","بشام","پلوله","ثنام","جوین",
 "حیات","خصدرا","خعمرا","خکاوه","خودکفا","داراب","شارپیلن","شصفها","شگستر",
 "فاهواز","فجوش","فسا","قجام","قیسنو","کصدف","کقزوی","کهرام","گشان",
 "لزما","لپیام","لکما","مرقام","ومشان","وملت"
]
OUT = Path("data/market_history_user33")
BRANCH = os.getenv("CHECKPOINT_BRANCH", "market-history-user33-checkpoints")
FIELDS = history.FIELDS

def checkpoint(paths, message):
    subprocess.run(["git","add",*[str(p) for p in paths]],check=True)
    if subprocess.run(["git","diff","--cached","--quiet"]).returncode != 0:
        subprocess.run(["git","commit","-m",message],check=True)
        subprocess.run(["git","push","origin","HEAD:"+BRANCH],check=True)

def main():
    params=["market=0"]+[f"paperTypes[{i}]={i+1}" for i in range(9)]
    params += ["withBestLimits=false","hEven=0","RefID=0"]
    payload=scan_market.get_json(scan_market.BASE+"/ClosingPrice/GetMarketWatch?"+"&".join(params))
    rows=scan_market.find_list(payload,("marketwatch","marketWatch","closingPriceInfo"))
    found={}
    for item in rows:
        if not isinstance(item,dict): continue
        ins=item.get("instrument") if isinstance(item.get("instrument"),dict) else {}
        price=item.get("closingPriceInfo") if isinstance(item.get("closingPriceInfo"),dict) else item
        src=ins or item
        sym=str(scan_market.first(src,"lva","lVal18AFC","lVal18","symbol","ticker",
                    default=scan_market.first(src,"lvc","lVal30","name"))).strip()
        code=str(scan_market.first(src,"insCode","inscode","ins_code",
                    default=scan_market.first(price,"insCode","inscode","ins_code"))).strip()
        if not code or code=="0" or sym not in SYMBOLS or sym.endswith("3"): continue
        base=scan_market.num(src,"baseVol","baseVolume","bv") or scan_market.num(item,"baseVol","baseVolume","bv")
        if base <= 1: continue
        found[sym]={"symbol":sym,"name":str(scan_market.first(src,"lvc","lVal30","name","companyName",default=sym)),
                    "insCode":code,"baseVol":base,
                    "asset_type":scan_market.classify_instrument(src,item,sym,sym)}
    OUT.mkdir(parents=True,exist_ok=True)
    (OUT/"requested_symbols.json").write_text(json.dumps(SYMBOLS,ensure_ascii=False,indent=2),encoding="utf-8")
    checkpoint([OUT/"requested_symbols.json"],"Save user 33-symbol extraction list")
    results=[]; failures=[]; missing=[]
    for i,sym in enumerate(SYMBOLS,1):
        item=found.get(sym)
        if not item:
            missing.append(sym)
            print(f"NOT_FOUND_OR_BASEVOL_MISMATCH {i}/33 {sym}",flush=True)
            continue
        raw=OUT/f"{i:02d}_{sym}_raw.csv.gz"
        adj=OUT/f"{i:02d}_{sym}_adjusted.csv.gz"
        meta=OUT/f"{i:02d}_{sym}.json"
        if meta.exists():
            old=json.loads(meta.read_text(encoding="utf-8"))
            if old.get("success") and old.get("insCode")==item["insCode"]:
                results.append(old); print(f"RESUME_EXISTING {i}/33 {sym}",flush=True); continue
        try:
            data=history.fetch_one(item)
            data["symbol"]=sym; data["asset_type"]=item["asset_type"]
            for path,key in ((raw,"raw"),(adj,"adjusted")):
                with gzip.open(path,"wt",encoding="utf-8",newline="",compresslevel=3) as f:
                    w=csv.DictWriter(f,fieldnames=FIELDS); w.writeheader(); w.writerows(data[key])
            record={"success":True,"ordinal":i,"symbol":sym,"insCode":item["insCode"],
                    "baseVol":item["baseVol"],"rows":len(data["raw"]),"latest_date":data["latest_date"],
                    "events":data["events"],"saved_at_utc":datetime.now(timezone.utc).isoformat()}
            meta.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding="utf-8")
            checkpoint([raw,adj,meta],f"Checkpoint user33 {i:02d} {sym}")
            results.append(record)
            print(f"SUCCESS {i}/33 {sym} rows={record['rows']} events={record['events']}",flush=True)
        except Exception as e:
            record={"success":False,"ordinal":i,"symbol":sym,"insCode":item["insCode"],"error":str(e)[:500]}
            meta.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding="utf-8")
            checkpoint([meta],f"Record user33 failure {i:02d} {sym}")
            failures.append(record)
            print(f"FAILED {i}/33 {sym}: {str(e)[:250]}",flush=True)
    manifest={"generated_at_utc":datetime.now(timezone.utc).isoformat(),"requested":len(SYMBOLS),
              "matched_live_basevol_gt1":len(found),"succeeded":len([x for x in results if x.get("success")]),
              "failed":len(failures),"missing_or_basevol_mismatch":missing,
              "failures":failures,"results":results}
    p=OUT/"manifest.json"; p.write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf-8")
    checkpoint([p],"Finalize user 33-symbol extraction manifest")
    print("FINAL "+json.dumps(manifest,ensure_ascii=False),flush=True)

if __name__=="__main__": main()
