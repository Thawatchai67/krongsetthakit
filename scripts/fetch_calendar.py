#!/usr/bin/env python3
"""กรองเศรษฐกิจ: ปฏิทินตัวเลขเศรษฐกิจที่แรง + สรุปผลหลังข่าวออก -> docs/calendar.json

แหล่งข้อมูล (ฟรี ไม่ต้องสมัคร): ปฏิทินรายสัปดาห์ของ Forex Factory (nfs.faireconomy.media)
มีเฉพาะสัปดาห์ปัจจุบัน (ขึ้นสัปดาห์ใหม่ทุกวันอาทิตย์) และไม่มีตัวเลขที่ออกจริง
จึงเก็บประวัติเองใน data/calendar_history.json แล้วใช้หัวข่าวใน docs/news.json บอกว่าตัวเลขออกมาเท่าไร

สิ่งที่เขียนลง docs/calendar.json:
- upcoming: ตัวเลขแรงที่จะออกใน 7 วันข้างหน้า (เวลาไทย) + ทำไมสำคัญ + ถ้าสูง/ต่ำกว่าคาดมักกระทบอะไร
- recent: ตัวเลขแรงที่ออกไปแล้วใน 14 วัน + ทอง/ค่าเงิน/บอนด์/หุ้น ขยับเท่าไรหลังจากนั้น 1/3/5 วันทำการ
  + หัวข่าวที่เกี่ยวข้อง

กติกาตีความอยู่ใน config/calendar_rules.json (แก้เองได้) เป็นแนวโน้มทั่วไป ไม่ได้เกิดทุกครั้ง

รัน: python scripts/fetch_calendar.py
ทดสอบไม่ต่อเน็ต: python scripts/fetch_calendar.py --from-file tests/samples/ff_calendar.json --out /tmp/cal.json --no-save
"""
import argparse
import bisect
import hashlib
import json
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
FEED_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
TZ_TH = ZoneInfo("Asia/Bangkok")
UPCOMING_DAYS = 7
RECENT_DAYS = 14
KEEP_DAYS = 400
HORIZONS = (1, 3, 5)

# สินทรัพย์ที่ดูผลหลังข่าว: ชุดราคาใน price_history, เขตเวลาของวันที่ในข้อมูล, เวลาปิด (ชม., นาที), หน่วย
ASSETS = [
    {"key": "GOLD", "name": "ทองคำ", "series": "M:GOLD", "tz": "America/New_York", "close": (17, 0), "unit": "pct"},
    {"key": "USDTHB", "name": "ดอลลาร์/บาท", "series": "M:USDTHB", "tz": "Europe/London", "close": (23, 59), "unit": "pct"},
    {"key": "US10Y", "name": "บอนด์สหรัฐ 10 ปี", "series": "M:US10Y", "tz": "America/New_York", "close": (16, 0), "unit": "abs"},
    {"key": "SPX", "name": "S&P 500", "series": "^GSPC", "tz": "America/New_York", "close": (16, 0), "unit": "pct"},
    {"key": "SET", "name": "SET", "series": "^SET", "tz": "Asia/Bangkok", "close": (16, 30), "unit": "pct"},
]


def log(msg):
    print(msg, file=sys.stderr)


def load(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default


def iso_utc(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_utc(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def fetch_feed(from_file=None):
    if from_file:
        return json.loads(Path(from_file).read_text(encoding="utf-8"))
    req = urllib.request.Request(FEED_URL, headers={"User-Agent": "Mozilla/5.0 (compatible; krongsetthakit/1.0)"})
    with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310
        return json.loads(r.read().decode("utf-8"))


# ---------------------------------------------------------------- rules
def build_rules(cfg):
    out = []
    for r in cfg.get("rules", []):
        out.append(dict(r, rx=re.compile(r["match"], re.IGNORECASE)))
    return out


def match_rule(ev, rules):
    for r in rules:
        if ev["country"] in r.get("countries", [ev["country"]]) and r["rx"].search(ev["title"]):
            return r
    return None


def wanted(ev, rule, cfg):
    """เก็บเฉพาะตัวเลขที่แรงของประเทศที่กระทบไทย หรือที่มีกฎตีความ"""
    if ev["country"] not in cfg.get("countries", []):
        return False
    if ev["impact"] == "High":
        return True
    return ev["impact"] == "Medium" and rule is not None


# ---------------------------------------------------------------- events
def normalize(raw, rules, cfg):
    out = []
    for e in raw:
        try:
            dt = datetime.fromisoformat(e["date"])
        except (KeyError, ValueError):
            continue
        ev = {
            "title": e.get("title", "").strip(), "country": e.get("country", ""), "impact": e.get("impact", ""),
            "forecast": e.get("forecast") or "", "previous": e.get("previous") or "",
            "time": iso_utc(dt),
            # เวลา 00:00 ตามเวลาสหรัฐ มักแปลว่า "ทั้งวัน/ยังไม่ระบุเวลา"
            "all_day": dt.hour == 0 and dt.minute == 0,
        }
        rule = match_rule(ev, rules)
        if not wanted(ev, rule, cfg):
            continue
        ev["id"] = hashlib.sha1(f"{ev['country']}|{ev['title']}|{ev['time']}".encode()).hexdigest()[:12]
        ev["rule"] = rule["id"] if rule else None
        out.append(ev)
    return out


def merge_history(hist, events, now):
    for ev in events:
        old = hist.get(ev["id"], {})
        hist[ev["id"]] = dict(old, **ev, first_seen=old.get("first_seen", iso_utc(now)))
    cutoff = iso_utc(now - timedelta(days=KEEP_DAYS))
    for k in [k for k, v in hist.items() if v["time"] < cutoff]:
        del hist[k]
    return hist


# ---------------------------------------------------------------- market reaction
def base_index(dates, ev_time, asset):
    """ตำแหน่งราคาปิดล่าสุดก่อนข่าวออก (ตามวันที่และเวลาปิดของตลาดนั้น)"""
    local = ev_time.astimezone(ZoneInfo(asset["tz"]))
    day = local.strftime("%Y-%m-%d")
    after_close = (local.hour, local.minute) >= asset["close"]
    i = bisect.bisect_right(dates, day) if after_close else bisect.bisect_left(dates, day)
    return i - 1


def reaction(ev_time, history, now):
    """การขยับของสินทรัพย์หลังข่าว {asset: {d1, d3, d5, live}} (% หรือ จุด สำหรับบอนด์ยีลด์)
    live = รายการช่วงที่ราคาวันสุดท้ายยังไม่ปิด (ตัวเลขยังเปลี่ยนได้)"""
    out = {}
    for a in ASSETS:
        h = history.get(a["series"]) or {}
        dates = sorted(h)
        i0 = base_index(dates, ev_time, a)
        if i0 < 0:
            continue
        local_now = now.astimezone(ZoneInfo(a["tz"]))
        today_open = (local_now.hour, local_now.minute) < a["close"]
        today = local_now.strftime("%Y-%m-%d")
        moves = {}
        for n in HORIZONS:
            if i0 + n < len(dates):
                p0, pn = h[dates[i0]], h[dates[i0 + n]]
                if a["unit"] == "abs":
                    moves[f"d{n}"] = round(pn - p0, 3)
                elif p0:
                    moves[f"d{n}"] = round((pn / p0 - 1) * 100, 2)
                if dates[i0 + n] >= today and today_open:
                    moves.setdefault("live", []).append(f"d{n}")
        if moves:
            out[a["key"]] = moves
    return out


def related_news(ev_time, rules, news, limit=3):
    """หัวข่าวที่น่าจะพูดถึงตัวเลขนี้ (12 ชม. ก่อน ถึง 3 วันหลังข่าวออก) ข่าวหลังตัวเลขออกขึ้นก่อน"""
    pats = [r["news"] for r in rules if r.get("news")]
    if not pats:
        return []
    rx = re.compile("|".join(f"(?:{p})" for p in pats), re.IGNORECASE)
    t = ev_time
    lo, hi, t_s = iso_utc(t - timedelta(hours=12)), iso_utc(t + timedelta(days=3)), iso_utc(t)
    hits = [it for it in news.get("items", []) if lo <= it.get("published", "") <= hi and rx.search(it.get("title", ""))]
    hits.sort(key=lambda it: (it["published"] < t_s, it["published"]))
    return [{"title": it["title"], "link": it["link"], "source": it.get("source", ""), "published": it["published"],
             "before": it["published"] < t_s} for it in hits[:limit]]


def group(events):
    """ตัวเลขที่ออกพร้อมกัน (ประเทศ+เวลาเดียวกัน) รวมเป็นหนึ่งเหตุการณ์"""
    g = {}
    for ev in sorted(events, key=lambda e: (e["time"], e["impact"] != "High", e["title"])):
        g.setdefault((ev["time"], ev["country"]), []).append(ev)
    return list(g.values())


def describe(items, rules_by_id):
    lead = items[0]
    rules = []
    for e in items:
        r = rules_by_id.get(e["rule"])
        if r and r not in rules:
            rules.append(r)
    out = {
        "time": lead["time"], "all_day": lead["all_day"], "country": lead["country"],
        "impact": "High" if any(e["impact"] == "High" for e in items) else lead["impact"],
        "items": [{"title": e["title"], "forecast": e["forecast"], "previous": e["previous"], "impact": e["impact"],
                   "rule_id": e["rule"]} for e in items],
        "rules": [{k: r[k] for k in ("id", "name", "why", "higher", "lower", "labels") if k in r} for r in rules],
    }
    return out, rules


def build(hist, rules, news, history, now):
    rules_by_id = {r["id"]: r for r in rules}
    now_s = iso_utc(now)
    up_hi = iso_utc(now + timedelta(days=UPCOMING_DAYS))
    rc_lo = iso_utc(now - timedelta(days=RECENT_DAYS))
    upcoming, recent = [], []
    for items in group(hist.values()):
        t = items[0]["time"]
        if now_s <= t <= up_hi:
            upcoming.append(describe(items, rules_by_id)[0])
        elif rc_lo <= t < now_s and any(e["impact"] == "High" for e in items):
            d, ev_rules = describe(items, rules_by_id)
            d["reaction"] = reaction(parse_utc(t), history, now)
            d["news"] = related_news(parse_utc(t), ev_rules, news)
            recent.append(d)
    upcoming.sort(key=lambda x: x["time"])
    recent.sort(key=lambda x: x["time"], reverse=True)
    return upcoming, recent


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--from-file", help="อ่านปฏิทินจากไฟล์แทนการดึงจากเน็ต")
    ap.add_argument("--out", default=str(ROOT / "docs" / "calendar.json"))
    ap.add_argument("--no-save", action="store_true", help="ไม่บันทึกประวัติลง data/")
    ap.add_argument("--now", help="เวลาอ้างอิง (ทดสอบ) เช่น 2026-11-25T00:00:00Z")
    args = ap.parse_args()

    now = parse_utc(args.now) if args.now else datetime.now(timezone.utc).replace(microsecond=0)
    cfg = load(ROOT / "config" / "calendar_rules.json", {})
    rules = build_rules(cfg)
    hist_path = ROOT / "data" / "calendar_history.json"
    hist = load(hist_path, {})
    error = None
    try:
        raw = fetch_feed(args.from_file)
        events = normalize(raw, rules, cfg)
        merge_history(hist, events, now)
        log(f"ปฏิทิน: {len(raw)} รายการ เก็บ {len(events)}")
    except Exception as exc:  # noqa: BLE001
        error = str(exc)[:160]
        log(f"[ผิดพลาด] ดึงปฏิทินไม่สำเร็จ ใช้ข้อมูลเดิม: {error}")

    news = load(ROOT / "docs" / "news.json", {})
    history = load(ROOT / "data" / "price_history.json", {})
    upcoming, recent = build(hist, rules, news, history, now)
    data = {
        "generated_at": iso_utc(now), "source": "Forex Factory", "error": error,
        "assets": [{"key": a["key"], "name": a["name"], "unit": a["unit"]} for a in ASSETS],
        "upcoming": upcoming, "recent": recent,
    }
    Path(args.out).write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    if not args.no_save:
        hist_path.write_text(json.dumps(dict(sorted(hist.items(), key=lambda kv: kv[1]["time"])), ensure_ascii=False, indent=1) + "\n",
                             encoding="utf-8")
    log(f"เขียน {args.out}: กำลังจะออก {len(upcoming)}, ออกแล้ว {len(recent)}")


if __name__ == "__main__":
    main()
