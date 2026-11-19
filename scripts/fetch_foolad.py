#!/usr/bin/env python3
"""Download raw and corporate-action-adjusted daily history for a TSETMC instrument."""
from __future__ import annotations
import csv, json, os, sys, time, urllib.request
from pathlib import Path

INS_CODE = os.getenv("TSETMC_INS_CODE", "46348559193224090")
SYMBOL = os.getenv("TSETMC_SYMBOL", "فولاد")
BASE = "https://cdn.tsetmc.com/api"
OUT_DIR = Path('data')
CSV_PATH = OUT_DIR / 'foolad_daily.csv'
META_PATH = OUT_DIR / 'foolad_instrument.json'
CHART_FIELDS = ['date', 'pmax', 'pmin', 'pf', 'pl', 'tvol', 'pc']

def get_json(url, attempts=4):
    headers = {'User-Agent':'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36','Accept':'application/json, text/plain, */*','Referer':'https://www.tsetmc.com/'}
    last = None
    for n in range(attempts):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.loads(r.read().decode('utf-8-sig'))
        except Exception as e:
            last = e
            if n + 1 < attempts: time.sleep(2 ** n)
    raise RuntimeError('Request failed: ' + url + ' : ' + str(last))

def rows_from(payload, keys):
    if isinstance(payload, list): return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in keys:
            value = payload.get(key)
            if isinstance(value, list): return [x for x in value if isinstance(x, dict)]
    raise ValueError('Expected list response; keys=' + str(list(payload.keys()) if isinstance(payload,dict) else type(payload).__name__))

def num(value):
    try: return float(value or 0)
    except (TypeError, ValueError): return 0.0

def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    info_url = BASE + '/Instrument/GetInstrumentInfo/' + INS_CODE
    daily_url = BASE + '/ClosingPrice/GetClosingPriceDailyList/' + INS_CODE + '/0'
    adjust_url = BASE + '/ClosingPrice/GetPriceAdjustList/' + INS_CODE
    shares_url = BASE + '/Instrument/GetInstrumentShareChange/' + INS_CODE
    info_payload = get_json(info_url)
    info = info_payload.get('instrumentInfo', info_payload) if isinstance(info_payload,dict) else {}
    daily = rows_from(get_json(daily_url), ['closingPriceDaily'])
    if len(daily) < 100: raise ValueError('Daily history too short: ' + str(len(daily)))
    adjustments = rows_from(get_json(adjust_url), ['priceAdjust'])
    share_changes = rows_from(get_json(shares_url), ['instrumentShareChange'])
    # Deduplicate share-change records; exclude pre-IPO placeholder share counts.
    unique = {}
    for e in share_changes:
        d = int(num(e.get('dEven'))); old = num(e.get('numberOfShareOld')); new = num(e.get('numberOfShareNew'))
        if d and old >= 1000 and new >= 1000:
            unique[(d, old, new)] = {'date':d, 'ratio':old/new, 'source':'share_change'}
    events = []
    adjust_dates = []
    for e in adjustments:
        d = int(num(e.get('dEven'))); before = num(e.get('pClosingNotAdjusted')); after = num(e.get('pClosing'))
        if d and before > 0 and after > 0:
            events.append({'date':d, 'ratio':after/before, 'source':'price_adjust'})
            adjust_dates.append(d)
    # Share-change endpoint fills capital events missing from PriceAdjustList.
    for e in unique.values():
        if not any(abs(e['date'] - d) <= 7 for d in adjust_dates): events.append(e)
    events.sort(key=lambda x:x['date'])
    # Build and validate separate raw and adjusted OHLCV histories.
    raw_rows=[]; adj_rows=[]; seen=set()
    for r in daily:
        d = int(num(r.get('dEven') or r.get('date')))
        if not d or d in seen: continue
        seen.add(d)
        factor=1.0
        for ev in events:
            if ev['date'] > d: factor *= ev['ratio']
        vals={'date':str(d),'pmax':num(r.get('priceMax')),'pmin':num(r.get('priceMin')),'pf':num(r.get('priceFirst')),'pl':num(r.get('pDrCotVal')),'tvol':num(r.get('qTotTran5J')),'pc':num(r.get('pClosing'))}
        raw_rows.append(vals)
        adj = dict(vals)
        for k in ('pmax','pmin','pf','pl','pc'): adj[k] = vals[k] * factor
        adj_rows.append(adj)
    raw_rows.sort(key=lambda x:x['date']); adj_rows.sort(key=lambda x:x['date'])
    if len(adj_rows) < 100: raise ValueError('Validated history too short')
    for name, data in [('foolad_chart_raw.csv',raw_rows),('foolad_chart_adjusted.csv',adj_rows)]:
        with (OUT_DIR/name).open('w',encoding='utf-8-sig',newline='') as f:
            w=csv.DictWriter(f,fieldnames=CHART_FIELDS); w.writeheader(); w.writerows(data)
    # Keep original daily API records for audit and execution-price reference.
    old_rows={}
    if CSV_PATH.exists():
        with CSV_PATH.open('r',encoding='utf-8-sig',newline='') as f:
            for x in csv.DictReader(f):
                if x.get('date'): old_rows[x['date']]=x
    for r in daily:
        d=str(r.get('dEven') or r.get('date') or '')
        if d: old_rows[d]={'symbol':SYMBOL,'date':d,**{k:r.get(k,'') for k in ['insCode','priceFirst','pClosing','pDrCotVal','priceMin','priceMax','priceYesterday','priceChange','zTotTran','qTotTran5J','qTotCap']}}
    fields=['symbol','date','insCode','priceFirst','pClosing','pDrCotVal','priceMin','priceMax','priceYesterday','priceChange','zTotTran','qTotTran5J','qTotCap']
    with CSV_PATH.open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore'); w.writeheader(); w.writerows(sorted(old_rows.values(),key=lambda x:x['date']))
    with META_PATH.open('w',encoding='utf-8') as f:
        json.dump({'symbol':SYMBOL,'insCode':INS_CODE,'instrument_info':info,'daily_api':daily_url,'raw_rows':len(raw_rows),'adjusted_rows':len(adj_rows),'price_adjust_events':len(adjustments),'share_change_events_unique':len(unique),'merged_events':len(events),'adjustment_method':'TSETMC PriceAdjustList ratio pClosing/pClosingNotAdjusted plus uncovered share changes old/new; share events within 7 calendar days of a price-adjust event are deduplicated','warning':'Adjusted OHLC values are calculated from TSETMC corporate-action events. Verify event coverage against charting provider before using automated trade signals.','adjustment_events':events},f,ensure_ascii=False,indent=2)
    print('OK raw={}, adjusted={}, price_adjust={}, unique_share_changes={}, merged_events={}'.format(len(raw_rows),len(adj_rows),len(adjustments),len(unique),len(events)))
    print('Latest date:',raw_rows[-1]['date'])
if __name__ == '__main__':
    try: main()
    except Exception as e:
        print('ERROR:',e,file=sys.stderr); raise