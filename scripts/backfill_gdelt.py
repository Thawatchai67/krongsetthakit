#!/usr/bin/env python3
"""ดึงหัวข่าวต่างประเทศย้อนหลังจาก GDELT (ค้นได้ตั้งแต่ 1 ม.ค. 2017) แล้วบันทึกสัญญาณ

ขั้น 1 เก็บข่าว ไล่จากล่าสุดย้อนกลับ ทำต่อจากจุดเดิมเองทุกครั้ง (จำไว้ใน data/gdelt_state.json):
    python scripts/backfill_gdelt.py collect --minutes 300
ขั้น 2 รวม data/gdelt/*.json ลง data/signal_log.json (รายการที่ได้จะมี "backfill": true):
    python scripts/backfill_gdelt.py merge

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
# รวมทุกหัวข้อในคำค้นเดียว ลดจำนวนครั้งที่เรียก (GDELT จำกัดความถี่) และเรียงตามความเกี่ยวข้อง
QUERIES = [
    '(oil OR crude OR brent OR gold OR tariff OR tariffs OR nvidia OR semiconductor OR chips'
    ' OR "rate cut" OR "rate hike" OR "cuts rates" OR "raises rates")',
]
STATE = ROOT / "data" / "gdelt_state.json"
PARTS = ROOT / "data" / "gdelt"


def fetch(query, start, end):
    params = {
        "query": f"{query} {DOMAINS} sourcelang:english",
        "mode": "ArtList", "format": "json", "maxrecords": "250", "sort": "HybridRel",
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


def collect(start, end, window_days, minutes):
    """เก็บข่าวทีละสัปดาห์ ไล่จากล่าสุดย้อนกลับไปถึง start
    เซฟลง data/gdelt/<ปี>.json และจำสัปดาห์เก่าสุดที่ทำแล้วไว้ใน data/gdelt_state.json ("oldest")
    ถ้าโดนจำกัดความถี่ จะรอแล้วลองสัปดาห์เดิมซ้ำจนหมดเวลา (ไม่ข้าม ไม่หยุดทั้งรอบ)"""
    rules = fn.load_json(ROOT / "config" / "rules.json")
    cats, sigs = fn.build_rules(rules)
    state = ts.load(STATE, {})
    w_end = datetime.strptime(state["oldest"], "%Y-%m-%d").replace(tzinfo=timezone.utc) if state.get("oldest") else end
    deadline = time.time() + minutes * 60
    PARTS.mkdir(parents=True, exist_ok=True)
    while w_end > start and time.time() < deadline:
        win = max(w_end - timedelta(days=window_days), start)
        try:
            arts = fetch(QUERIES[0], win, w_end)
        except Exception as exc:  # noqa: BLE001
            wait = 300 if "429" in str(exc) else 60
            print(f"[รอ {wait} วิ] {win:%Y-%m-%d}: {exc}", file=sys.stderr)
            time.sleep(wait)
            continue
        new = []
        for a in arts:
            try:
                it = to_item(a)
            except ValueError:
                continue
            if not it["title"]:
                continue
            _, _, sig_ids = fn.classify(it["title"], "", cats, sigs, rules["settings"])
            if sig_ids:
                new.append(dict(it, signals=sig_ids))
        part_path = PARTS / f"{win:%Y}.json"
        part = ts.load(part_path, {"items": []})
        links = {x["link"] for x in part["items"]}
        part["items"] += [x for x in new if x["link"] not in links]
        part_path.write_text(json.dumps(part, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
        state["oldest"] = f"{win:%Y-%m-%d}"
        STATE.write_text(json.dumps(state) + "\n", encoding="utf-8")
        print(f"{win:%Y-%m-%d}: ข่าว {len(arts)}, มีสัญญาณ {len(new)}", file=sys.stderr)
        w_end = win
        time.sleep(8)


def merge():
    """รวม data/gdelt/*.json ลง data/signal_log.json (ซ้ำกันไม่นับซ้ำ)"""
    rules = fn.load_json(ROOT / "config" / "rules.json")
    log_path = ROOT / "data" / "signal_log.json"
    log = ts.load(log_path, {})
    before = set(log)
    total = 0
    for p in sorted(PARTS.glob("*.json")):
        part = ts.load(p, {"items": []})
        total += len(part["items"])
        ts.add_entries(log, {"items": part["items"]}, rules)
    for k in set(log) - before:
        log[k]["backfill"] = True
    log_path.write_text(json.dumps(dict(sorted(log.items())), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"ข่าวย้อนหลังทั้งหมด {total}, เพิ่มรายการ {len(set(log) - before)}", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect")
    c.add_argument("--start", default="2017-01-01")
    c.add_argument("--window-days", type=int, default=7)
    c.add_argument("--minutes", type=float, default=300, help="ทำงานนานสุดกี่นาที แล้วเซฟไว้ทำต่อรอบหน้า")
    sub.add_parser("merge")
    args = ap.parse_args()
    if args.cmd == "collect":
        start = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        collect(start, datetime.now(timezone.utc) - timedelta(days=1), args.window_days, args.minutes)
    else:
        merge()


if __name__ == "__main__":
    main()
