#!/usr/bin/env python3
"""ดึงหัวข่าวต่างประเทศย้อนหลังจาก GDELT (ค้นได้ตั้งแต่ 1 ม.ค. 2017) แล้วบันทึกสัญญาณ

ขั้น 1 เก็บข่าว (แยกรันทีละปีพร้อมกันได้):
    python scripts/backfill_gdelt.py collect --start 2017-01-01 --end 2018-01-01 --out parts/2017.json
ขั้น 2 รวมทุกไฟล์ลง data/signal_log.json (รายการที่ได้จะมี "backfill": true):
    python scripts/backfill_gdelt.py merge parts/*.json

ใช้กฎเดียวกับ fetch_news.py จับสัญญาณจากหัวข่าว
"""
import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import fetch_news as fn  # noqa: E402
import track_signals as ts  # noqa: E402

API = "https://api.gdeltproject.org/api/v2/doc/doc"
DOMAINS = "(domainis:reuters.com OR domainis:cnbc.com OR domainis:marketwatch.com OR domainis:apnews.com OR domainis:finance.yahoo.com)"
QUERIES = [
    "(oil OR crude OR brent)",
    '("rate cut" OR "rate hike" OR "cuts rates" OR "raises rates")',
    "gold",
    "(tariff OR tariffs)",
    "(nvidia OR semiconductor OR chips)",
]


def fetch(query, start, end):
    params = {
        "query": f"{query} {DOMAINS} sourcelang:english",
        "mode": "ArtList", "format": "json", "maxrecords": "250", "sort": "DateAsc",
        "startdatetime": start.strftime("%Y%m%d%H%M%S"), "enddatetime": end.strftime("%Y%m%d%H%M%S"),
    }
    req = urllib.request.Request(API + "?" + urllib.parse.urlencode(params), headers={"User-Agent": "KrongSetthakit/0.1"})
    with urllib.request.urlopen(req, timeout=60) as r:
        body = r.read().decode("utf-8", "replace").strip()
    if not body.startswith("{"):
        if "limit requests" in body.lower():
            raise RuntimeError("429 rate limit")
        return []
    return json.loads(body).get("articles", [])


def to_item(a):
    pub = datetime.strptime(a.get("seendate", ""), "%Y%m%dT%H%M%SZ").strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"title": fn.clean_text(a.get("title")), "link": a.get("url", ""), "published": pub}


def collect(start, end, window_days):
    rules = fn.load_json(ROOT / "config" / "rules.json")
    cats, sigs = fn.build_rules(rules)
    items, seen, failed, requests = [], set(), 0, 0
    win = start
    while win < end:
        w_end = min(win + timedelta(days=window_days), end)
        for q in QUERIES:
            arts, ok = [], False
            for wait in (0, 20, 45, 90):  # โดนจำกัดความถี่ ให้รอแล้วลองใหม่
                time.sleep(wait)
                try:
                    arts = fetch(q, win, w_end)
                    ok = True
                    break
                except Exception as exc:  # noqa: BLE001
                    print(f"[ลองใหม่] {q[:20]} {win:%Y-%m-%d}: {exc}", file=sys.stderr)
            requests += 1
            failed += 0 if ok else 1
            for a in arts:
                try:
                    it = to_item(a)
                except ValueError:
                    continue
                if not it["title"] or it["link"] in seen:
                    continue
                seen.add(it["link"])
                _, _, sig_ids = fn.classify(it["title"], "", cats, sigs, rules["settings"])
                if sig_ids:
                    items.append(dict(it, signals=sig_ids))
            time.sleep(6)  # GDELT ขอให้ห่างกันอย่างน้อย 5 วินาที
        print(f"{win:%Y-%m-%d}: ข่าวที่มีสัญญาณ {len(items)}", file=sys.stderr)
        win = w_end
    return {"start": f"{start:%Y-%m-%d}", "end": f"{end:%Y-%m-%d}", "requests": requests, "failed": failed, "items": items}


def merge(paths):
    rules = fn.load_json(ROOT / "config" / "rules.json")
    log_path = ROOT / "data" / "signal_log.json"
    log = ts.load(log_path, {})
    before = set(log)
    total = 0
    for p in paths:
        part = json.loads(Path(p).read_text(encoding="utf-8"))
        total += len(part["items"])
        print(f"{p}: {part['start']}..{part['end']} ข่าว {len(part['items'])}, ดึงไม่ได้ {part['failed']}/{part['requests']}", file=sys.stderr)
        ts.add_entries(log, {"items": part["items"]}, rules)
    for k in set(log) - before:
        log[k]["backfill"] = True
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(json.dumps(dict(sorted(log.items())), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"รวมข่าวที่มีสัญญาณ {total}, เพิ่มรายการ {len(set(log) - before)}", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect")
    c.add_argument("--start", required=True)
    c.add_argument("--end", required=True)
    c.add_argument("--window-days", type=int, default=14)
    c.add_argument("--out", required=True)
    m = sub.add_parser("merge")
    m.add_argument("paths", nargs="+")
    args = ap.parse_args()
    if args.cmd == "collect":
        start = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        end = min(datetime.strptime(args.end, "%Y-%m-%d").replace(tzinfo=timezone.utc), datetime.now(timezone.utc))
        out = collect(start, end, args.window_days)
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(out, ensure_ascii=False) + "\n", encoding="utf-8")
    else:
        merge(args.paths)


if __name__ == "__main__":
    main()
