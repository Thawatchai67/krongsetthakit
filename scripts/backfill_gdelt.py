#!/usr/bin/env python3
"""ดึงหัวข่าวต่างประเทศย้อนหลังจาก GDELT แล้วบันทึกสัญญาณลง data/signal_log.json (ทำครั้งเดียว)

ใช้กฎเดียวกับ fetch_news.py จับสัญญาณจากหัวข่าว แล้วให้ track_signals.py วัดผลกับราคาย้อนหลัง
รายการที่ได้จากที่นี่จะมี "backfill": true

ใช้งาน:  python scripts/backfill_gdelt.py --days 85
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
    return json.loads(body).get("articles", []) if body.startswith("{") else []


def to_item(a):
    sd = a.get("seendate", "")  # 20261101T123000Z
    pub = datetime.strptime(sd, "%Y%m%dT%H%M%SZ").strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"title": fn.clean_text(a.get("title")), "link": a.get("url", ""), "published": pub}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=85)
    args = ap.parse_args()
    rules = fn.load_json(ROOT / "config" / "rules.json")
    cats, sigs = fn.build_rules(rules)
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=args.days)
    items, seen = [], set()
    win = start
    while win < end:
        w_end = min(win + timedelta(days=7), end)
        for q in QUERIES:
            arts = []
            for wait in (0, 20, 45, 90):  # โดน 429 (ถี่เกินไป) ให้รอแล้วลองใหม่
                time.sleep(wait)
                try:
                    arts = fetch(q, win, w_end)
                    break
                except Exception as exc:  # noqa: BLE001
                    print(f"[ผิดพลาด] {q} {win:%Y-%m-%d}: {exc}", file=sys.stderr)
            for a in arts:
                it = to_item(a)
                if not it["title"] or it["link"] in seen:
                    continue
                seen.add(it["link"])
                _, _, sig_ids = fn.classify(it["title"], "", cats, sigs, rules["settings"])
                if sig_ids:
                    items.append(dict(it, signals=sig_ids))
            time.sleep(8)  # GDELT ขอให้ห่างกันอย่างน้อย 5 วินาที
        print(f"{win:%Y-%m-%d}: สะสมข่าวที่มีสัญญาณ {len(items)}", file=sys.stderr)
        win = w_end
    log_path = ROOT / "data" / "signal_log.json"
    log = ts.load(log_path, {})
    before = set(log)
    added = ts.add_entries(log, {"items": items}, rules)
    for k in set(log) - before:
        log[k]["backfill"] = True
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(json.dumps(dict(sorted(log.items())), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"ข่าวที่มีสัญญาณ {len(items)} ข่าว, เพิ่มรายการ {added}", file=sys.stderr)


if __name__ == "__main__":
    main()
