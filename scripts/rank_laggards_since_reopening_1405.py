#!/usr/bin/env python3
"""Rank ordinary shares by official TSETMC adjusted total return since the 1405 reopening."""
import concurrent.futures
import json
import time
import urllib.request
from pathlib import Path

BASE = "https://members.tsetmc.com/tsev2/chart/data/Financial.aspx"
HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://www.tsetmc.com/",
    "Accept": "text/plain,*/*",
}
REOPEN_DATE = "20260519"  # 1405-02-29; first trading day after the 80-day closure
MIN_ROWS = 20
OUT = Path("reports")
OUT.mkdir(exist_ok=True)


def fetch_one(inst):
    code = str(inst["insCode"])
    last_error = None
    for attempt in range(4):
        try:
            url = f"{BASE}?i={code}&t=ph&a=1"
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=35) as response:
                body = response.read().decode("utf-8-sig", "replace")
            rows = []
            for line in body.strip().split(";"):
                cols = [x.strip() for x in line.split(",")]
                if len(cols) < 7:
                    continue
                date = cols[0]
                try:
                    high, volume, close = float(cols[1]), float(cols[5]), float(cols[6])
                except (ValueError, TypeError):
                    continue
                if len(date) != 8 or date < REOPEN_DATE or high <= 0 or close <= 0 or volume <= 0:
                    continue
                rows.append((date, close, high))
            rows.sort(key=lambda x: x[0])
            if len(rows) < MIN_ROWS:
                raise RuntimeError(f"only {len(rows)} valid sessions since reopening")
            first, latest = rows[0], rows[-1]
            if first[1] <= 0:
                raise RuntimeError("invalid adjusted baseline close")
            ret = (latest[1] / first[1] - 1) * 100
            return {
                "insCode": code,
                "symbol": inst.get("symbol", ""),
                "name": inst.get("name", ""),
                "asset_type": inst.get("asset_type", "share"),
                "baseVol": inst.get("baseVol"),
                "baseline_date": first[0],
                "baseline_adjusted_close": round(first[1], 4),
                "latest_date": latest[0],
                "latest_adjusted_close": round(latest[1], 4),
                "adjusted_return_pct": round(ret, 2),
                "sessions": len(rows),
                "delayed_reopening": first[0] > REOPEN_DATE,
                "source": "TSETMC Financial.aspx official adjusted chart (a=1)",
            }
        except Exception as exc:
            last_error = exc
            time.sleep(1.5 * (attempt + 1))
    return {
        "insCode": code,
        "symbol": inst.get("symbol", ""),
        "asset_type": inst.get("asset_type", "share"),
        "success": False,
        "error": str(last_error)[:300],
    }


universe = json.loads(Path("data/market_universe_all_unique.json").read_text(encoding="utf-8"))
seen = set()
shares = []
for item in universe.get("symbols", []):
    code = str(item.get("insCode", ""))
    if not code or code in seen or item.get("asset_type") != "share":
        continue
    seen.add(code)
    shares.append(item)

results = []
with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
    futures = [pool.submit(fetch_one, item) for item in shares]
    for index, future in enumerate(concurrent.futures.as_completed(futures), 1):
        result = future.result()
        results.append(result)
        print(f"LAGGARD {index}/{len(shares)} {result.get('symbol')} "
              f"{'OK' if 'adjusted_return_pct' in result else 'FAILED'}", flush=True)

successful = [x for x in results if "adjusted_return_pct" in x]
successful.sort(key=lambda x: x["adjusted_return_pct"])
failures = [x for x in results if "adjusted_return_pct" not in x]
returns = [x["adjusted_return_pct"] for x in successful]
median_return = sorted(returns)[len(returns)//2] if returns else None
bottom_quartile_count = max(1, (len(successful) + 3)//4) if successful else 0
payload = {
    "title": "Iran ordinary shares lagging since 1405 market reopening",
    "method": "Adjusted total return = (latest official adjusted close / first official adjusted close on or after reopening - 1) * 100",
    "source": "TSETMC Financial.aspx official adjusted chart API, parameter a=1",
    "reopening_date_gregorian": REOPEN_DATE,
    "reopening_date_jalali": "1405-02-29",
    "latest_data_date": max((x["latest_date"] for x in successful), default=None),
    "universe_file": "data/market_universe_all_unique.json",
    "universe_scope": "Unique ordinary shares only; fixed-income and other funds are excluded from this ranking.",
    "requested_shares": len(shares),
    "successful": len(successful),
    "failed": len(failures),
    "median_adjusted_return_pct": median_return,
    "bottom_quartile_count": bottom_quartile_count,
    "top50_laggards": successful[:50],
    "all_ranked": successful,
    "failures": failures,
}
Path("reports/laggards_since_reopening_1405.json").write_text(
    json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
)
lines = [
    "# نمادهای جامانده از رشد پس از بازگشایی بازار در سال ۱۴۰۵",
    "",
    "مبنا: نخستین روز معاملاتی پس از تعطیلی ۸۰روزه، ۲۹ اردیبهشت ۱۴۰۵.",
    "روش: بازده قیمت پایانی تعدیل‌شده رسمی TSETMC از اولین جلسه معاملاتی هر نماد در/پس از تاریخ بازگشایی تا آخرین داده موجود.",
    "جامعه آماری: سهام عادی موجود در فایل مرجع یکتای پروژه؛ صندوق‌ها برای جلوگیری از اختلاط صندوق‌های درآمد ثابت با سهام حذف شده‌اند.",
    f"موفق: {len(successful)} از {len(shares)} | ناموفق: {len(failures)} | میانه بازده: {median_return}%.",
    "",
    "| رتبه | نماد | بازده تعدیل‌شده | قیمت مبنا | آخرین قیمت تعدیل‌شده | تاریخ مبنا | آخرین تاریخ | جلسات |",
    "|---:|---|---:|---:|---:|---|---|---:|",
]
for rank, item in enumerate(successful[:50], 1):
    lines.append(
        f"| {rank} | {item['symbol']} | {item['adjusted_return_pct']:.2f}% "
        f"| {item['baseline_adjusted_close']:,.2f} | {item['latest_adjusted_close']:,.2f} "
        f"| {item['baseline_date']} | {item['latest_date']} | {item['sessions']} |"
    )
lines += ["", "## نمادهای ناموفق در دریافت داده", ""]
lines += [f"- {x['symbol']} ({x['insCode']}): {x.get('error', '')}" for x in failures]
Path("reports/laggards_since_reopening_1405.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
print(json.dumps({
    "requested": len(shares),
    "successful": len(successful),
    "failed": len(failures),
    "median_return_pct": median_return,
    "top20": successful[:20],
}, ensure_ascii=False))
