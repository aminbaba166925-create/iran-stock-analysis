#!/usr/bin/env python3
"""Recover the 17 user33 symbols through TSETMC instrument search, then save each history independently."""
import csv, gzip, json, os, subprocess, sys, time, urllib.parse
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0,"scripts")
import scan_market
import fetch_market_history_next100_v4 as history

SYMBOLS=["بایکا","پردیس","زندگی","بکهنوج","بشام","جوین","حیات","خکاوه","خودکفا","شارپیلن","قیسنو","کصدف","کقزوی","کهرام","لزما","لپیام","لکما"]
OUT=Path("data/market_history_user33")
BRANCH=os.getenv("CHECKPOINT_BRANCH","market-history-user33-checkpoints")
FIELDS=history.FIELDS
BASE=scan_market.BASE

def checkpoint(paths,msg):
    subprocess.run(["git","add",*[str(p) for p in paths]],check=True)
    if subprocess.run(["git","diff","--cached","--quiet"]).returncode==0:return
    subprocess.run(["git","commit","-m",msg],check=True)
    last=None
    for attempt in range(5):
        try:
            subprocess.run(["git","push","origin","HEAD:"+BRANCH],check=True);return
        except subprocess.CalledProcessError as e:
            last=e
            subprocess.run(["git","fetch","origin",BRANCH],check=True)
            subprocess.run(["git","rebase","FETCH_HEAD"],check=True)
    raise RuntimeError("checkpoint push failed") from last

def rows(payload):
    return scan_market.find_list(payload,("instrumentSearch","instrumentSearchResult","instruments","instrument","instrumentList"))

def discover(sym):
    # Search endpoint is independent of the live market-watch/base-volume filter.
    urls=[
      BASE+"/Instrument/GetInstrumentSearch/"+urllib.parse.quote(sym,safe=""),
      BASE+"/Instrument/GetInstrumentSearch/"+urllib.parse.quote(sym,safe="")+"/0"
    ]
    errors=[]
    for url in urls:
        try:
            payload=scan_market.get_json(url,attempts=2)
            for row in rows(payload):
                if not isinstance(row,dict):continue
                ins=row.get("instrument") if isinstance(row.get("instrument"),dict) else row
                symbol=str(scan_market.first(ins,"lva","lVal18AFC","lVal18","symbol","ticker",default="")).strip()
                name=str(scan_market.first(ins,"lvc","lVal30","name","companyName",default=symbol))
                code=str(scan_market.first(ins,"insCode","inscode","ins_code",default="")).strip()
                if symbol==sym and code and code!="0":
                    typ=scan_market.classify_instrument(ins,row,symbol,name)
                    if typ:
                        return {"symbol":sym,"name":name,"insCode":code,"baseVol":scan_market.num(ins,"baseVol","baseVolume","bv"),"asset_type":typ}
        except Exception as e: errors.append(str(e)[:180])
    return None

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    results=[]; unresolved=[]; failures=[]
    for n,sym in enumerate(SYMBOLS,1):
        meta=OUT/f"{n:02d}_{sym}.json"
        old={}
        if meta.exists():
            try:old=json.loads(meta.read_text(encoding="utf-8"))
            except Exception:pass
        if old.get("success") and (OUT/f"{n:02d}_{sym}_raw.csv.gz").exists() and (OUT/f"{n:02d}_{sym}_adjusted.csv.gz").exists():
            results.append(old);print(f"ALREADY_SAVED {sym}",flush=True);continue
        try:
            item=discover(sym)
            if not item:
                rec={"success":False,"status":"instrument_search_no_exact_match","ordinal":n,"symbol":sym,"checked_at_utc":datetime.now(timezone.utc).isoformat()}
                meta.write_text(json.dumps(rec,ensure_ascii=False,indent=2),encoding="utf-8")
                checkpoint([meta],f"Record unresolved history symbol {sym}")
                unresolved.append(sym);print(f"UNRESOLVED {sym}",flush=True);continue
            data=history.fetch_one(item)
            data["symbol"]=sym;data["asset_type"]=item["asset_type"]
            raw=OUT/f"{n:02d}_{sym}_raw.csv.gz";adj=OUT/f"{n:02d}_{sym}_adjusted.csv.gz"
            for path,key in ((raw,"raw"),(adj,"adjusted")):
                with gzip.open(path,"wt",encoding="utf-8",newline="",compresslevel=3) as f:
                    w=csv.DictWriter(f,fieldnames=FIELDS);w.writeheader();w.writerows(data[key])
            rec={"success":True,"ordinal":n,"symbol":sym,"insCode":item["insCode"],"baseVol":item["baseVol"],"rows":len(data["raw"]),"latest_date":data["latest_date"],"events":data["events"],"saved_at_utc":datetime.now(timezone.utc).isoformat()}
            meta.write_text(json.dumps(rec,ensure_ascii=False,indent=2),encoding="utf-8")
            checkpoint([raw,adj,meta],f"Save recovered symbol history {sym}")
            results.append(rec);print(f"SUCCESS {sym} rows={rec['rows']}",flush=True)
        except Exception as e:
            rec={"success":False,"ordinal":n,"symbol":sym,"error":str(e)[:500],"checked_at_utc":datetime.now(timezone.utc).isoformat()}
            meta.write_text(json.dumps(rec,ensure_ascii=False,indent=2),encoding="utf-8")
            checkpoint([meta],f"Record recovered symbol error {sym}")
            failures.append(rec);print(f"FAILED {sym}: {str(e)[:250]}",flush=True)
    manifest={"generated_at_utc":datetime.now(timezone.utc).isoformat(),"requested":len(SYMBOLS),"succeeded":len(results),"unresolved":unresolved,"failed":failures,"results":results}
    p=OUT/"recovery_17_manifest.json";p.write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf-8")
    checkpoint([p],"Finalize 17-symbol history recovery")
    print("FINAL "+json.dumps(manifest,ensure_ascii=False),flush=True)
if __name__=="__main__":main()
