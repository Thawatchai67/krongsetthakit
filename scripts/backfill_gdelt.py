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
# คำค้นต้องสั้น: GDELT ตอบ "Your query was too short or too long" ถ้ารวมทุกหัวข้อในคำค้นเดียว
QUERIES = [
    "(oil OR crude OR brent)",
    '("rate cut" OR "rate hike" OR "cuts rates" OR "raises rates")',
    "gold",
    "(tariff OR tariffs)",
    "(nvidia OR semiconductor OR chips)",
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
    if not body:
        return []  # ช่วงนั้นไม่มีข่าวจริงๆ
    if not body.startswith("{"):
        # ข้อความ error จาก GDELT (เช่น คำค้นยาวเกิน, ถี่เกิน) ต้องไม่ถือว่าเป็นผลว่าง
        raise RuntimeError("GDELT: " + body[:120])
    return json.loads(body).get("articles", [])


def to_item(a):
    pub = datetime.strptime(a.get("seendate", ""), "%Y%m%dT%H%M%SZ").strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"title": fn.clean_text(a.get("title")), "link": a.get("url", ""), "published": pub}


def collect(start, end, window_days, minutes):
    """เก็บข่าวทีละช่วง (ค่าเริ่มต้น 90 วัน) ไล่จากล่าสุดย้อนกลับไปถึง start
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
        # คำค้นที่ทำเสร็จแล้วในช่วงนี้ (หมดเวลากลางช่วง รอบหน้าไม่ต้องเริ่มใหม่ทั้งช่วง)
        done = state.get("done", []) if state.get("win_end") == f"{w_end:%Y-%m-%d}" else []
        for qi, q in enumerate(QUERIES):
            if qi in done:
                continue
            arts, ok, wait = [], False, 0
            while time.time() < deadline:
                time.sleep(wait)
                try:
                    arts = fetch(q, win, w_end)
                    ok = True
                    break
                except Exception as exc:  # noqa: BLE001
                    wait = min(300, max(30, wait * 2))  # ถี่เกิน: รอนานขึ้นเรื่อยๆ แล้วลองใหม่
                    print(f"[รอ {wait} วิ] {win:%Y-%m-%d} {q[:16]}: {exc}", file=sys.stderr)
            if not ok:
                print(f"หมดเวลาที่ {win:%Y-%m-%d}: รอบหน้าทำต่อจากคำค้นนี้", file=sys.stderr)
                return
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
            done = done + [qi]
            state.update(win_end=f"{w_end:%Y-%m-%d}", done=done)
            STATE.write_text(json.dumps(state) + "\n", encoding="utf-8")
            print(f"{win:%Y-%m-%d}..{w_end:%Y-%m-%d} {q[:16]}: ได้ {len(arts)} ข่าว มีสัญญาณ {len(new)}", file=sys.stderr)
            time.sleep(15)  # เว้นระยะให้ GDELT
        state = {"oldest": f"{win:%Y-%m-%d}"}
        STATE.write_text(json.dumps(state) + "\n", encoding="utf-8")
        w_end = win


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
    c.add_argument("--window-days", type=int, default=90)
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
