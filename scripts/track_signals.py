#!/usr/bin/env python3
"""กรองเศรษฐกิจ: เก็บสถิติว่าสัญญาณที่ระบบบอก แม่นจริงไหม

ทุกครั้งที่รัน:
1. อ่านข่าวที่มีสัญญาณจาก docs/news.json แล้วบันทึกลง data/signal_log.json
   (หนึ่งรายการต่อ สัญญาณ x หุ้น x วัน ข่าวซ้ำวันเดียวกันนับครั้งเดียว)
2. เทียบราคาปิดก่อนวันข่าว กับราคาปิดหลังจากนั้น 1, 5, 20 วันทำการ (จาก data/price_history.json)
   และเทียบกับดัชนีตลาด (SET หรือ S&P 500) ในช่วงเดียวกัน
3. สรุปผลลง docs/stats.json ให้หน้าเว็บแสดง

นับ 2 แบบ:
- รายหุ้น (d1/d5/d20): หนึ่งครั้งต่อ สัญญาณ x หุ้น x วัน  ข่าวเดียวที่โยงกับ 8 หุ้นจึงนับ 8 ครั้ง
  ซึ่งไม่เป็นอิสระต่อกัน ตัวเลขจึงดูมั่นใจเกินจริง
- รายเหตุการณ์ (events): หนึ่งครั้งต่อ สัญญาณ x วัน  ผลของเหตุการณ์ = ชนะดัชนีเฉลี่ยของทุกหุ้นตามทิศที่ทาย
  และนับเฉพาะเหตุการณ์ที่ห่างจากครั้งก่อนของสัญญาณเดียวกันพอ (ไม่ให้ช่วงวัดผลซ้อนกัน)
  พร้อมช่วงความเชื่อมั่น 90% ใช้ตัวนี้เป็นตัวเลขหลัก

"ถูก" หมายถึง: สัญญาณ ▲ แล้วหุ้นชนะดัชนี หรือ ▼ แล้วหุ้นแพ้ดัชนี  (◆ จับตา ไม่นับถูกผิด)
"""
import bisect
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HORIZONS = (1, 5, 20)
EFFECT = {"ได้ประโยชน์": "up", "ถูกกดดัน": "down", "ต้องจับตา": "watch"}
BENCH = {"SET": "^SET", "US": "^GSPC"}
TZ_TH = timezone(timedelta(hours=7))
# ระยะห่างขั้นต่ำ (วันปฏิทิน) ระหว่างเหตุการณ์ของสัญญาณเดียวกัน ให้ช่วงวัดผล 1/5/20 วันทำการไม่ซ้อนกัน
EVENT_GAP_DAYS = {1: 1, 5: 7, 20: 28}
Z90 = 1.645


def load(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default


def news_date(iso):
    """วันที่ของข่าวตามเวลาไทย (YYYY-MM-DD)"""
    dt = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return dt.astimezone(TZ_TH).strftime("%Y-%m-%d")


def add_entries(log, news, rules):
    sigs = {s["id"]: s for s in rules["signals"]}
    added = 0
    for it in news.get("items", []):
        for sid in it.get("signals", []):
            s = sigs.get(sid)
            if not s:
                continue
            day = news_date(it["published"])
            for im in s["impacts"]:
                eff = EFFECT.get(im["effect"], "watch")
                for market, tickers in (("SET", im.get("set", [])), ("US", im.get("us", []))):
                    for tk in tickers:
                        key = f"{sid}|{tk}|{day}"
                        if key in log:
                            continue
                        log[key] = {
                            "signal": sid, "signal_name": s["name"], "ticker": tk, "market": market,
                            "effect": eff, "date": day, "news_title": it["title"], "news_link": it["link"],
                        }
                        added += 1
    return added


def prune(log, news, rules):
    """ลบรายการที่กฎปัจจุบันไม่ถือว่าเป็นสัญญาณแล้ว (หลังแก้กฎ สถิติเก่าต้องตามไปด้วย)
    - ข่าวย้อนหลัง (backfill มีแค่หัวข้อ): จัดหมวดหัวข้อใหม่ด้วยกฎปัจจุบัน
    - ข่าวที่ยังอยู่ใน news.json: ใช้สัญญาณที่ fetch_news เพิ่งคิดใหม่
    - ข่าวเก่ากว่านั้น: เก็บไว้ (ไม่มีเนื้อข่าวให้เช็กซ้ำ)
    คืนจำนวนที่ลบ"""
    import fetch_news as fn  # noqa: PLC0415
    cats, sigs = fn.build_rules(rules)
    live = {it["link"]: set(it.get("signals", [])) for it in news.get("items", [])}
    cache = {}
    removed = 0
    for key in list(log):
        e = log[key]
        if e.get("backfill"):
            t = e["news_title"]
            if t not in cache:
                cache[t] = set(fn.classify(t, "", cats, sigs, rules.get("settings", {}))[2])
            ok = e["signal"] in cache[t]
        elif e.get("news_link") in live:
            ok = e["signal"] in live[e["news_link"]]
        else:
            ok = True
        if not ok:
            del log[key]
            removed += 1
    return removed


def wilson(k, n, z=Z90):
    """ช่วงความเชื่อมั่นของสัดส่วน k/n (คืนเป็น %)"""
    if n == 0:
        return None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / d
    return [round((c - h) * 100, 1), round((c + h) * 100, 1)]


def event_block(entries):
    """สถิติรายเหตุการณ์ (สัญญาณ x วัน) ที่ไม่ซ้อนกัน ดูคำอธิบายบนสุดของไฟล์"""
    out = {}
    for n in HORIZONS:
        groups = {}
        for e in entries:
            x = e.get(f"x{n}")
            if x is None or e["effect"] == "watch":
                continue
            groups.setdefault((e["signal"], e["date"]), []).append(x if e["effect"] == "up" else -x)
        edges, last = [], {}
        for (sid, day), xs in sorted(groups.items(), key=lambda kv: (kv[0][1], kv[0][0])):
            d = datetime.strptime(day, "%Y-%m-%d")
            if sid in last and (d - last[sid]).days < EVENT_GAP_DAYS[n]:
                continue
            last[sid] = d
            edges.append(sum(xs) / len(xs))
        hits = sum(1 for v in edges if v > 0)
        out[f"d{n}"] = {
            "evaluated": len(edges),
            "hit_rate": round(hits / len(edges) * 100, 1) if edges else None,
            "avg_edge": round(sum(edges) / len(edges), 2) if edges else None,
            "ci": wilson(hits, len(edges)),
        }
    return out


_DATES = {}


def _dates(history, key):
    """รายการวันที่เรียงแล้ว (cache ไว้ เพราะเรียกซ้ำหลายพันครั้ง)"""
    if key not in _DATES:
        _DATES[key] = sorted(history.get(key) or {})
    return _DATES[key]


def evaluate(entry, history):
    """เติมผลลัพธ์ r1/r5/r20 (ผลตอบแทนหุ้น %) และ x1/x5/x20 (ชนะดัชนี %) เท่าที่มีข้อมูล"""
    h = history.get(entry["ticker"]) or {}
    b = history.get(BENCH[entry["market"]]) or {}
    dates = _dates(history, entry["ticker"])
    i0 = bisect.bisect_left(dates, entry["date"]) - 1   # ปิดล่าสุดก่อนวันข่าว
    if i0 < 0:
        return
    d0 = dates[i0]
    bdates = _dates(history, BENCH[entry["market"]])
    for n in HORIZONS:
        if f"r{n}" in entry or i0 + n >= len(dates):
            continue
        dn = dates[i0 + n]
        r = (h[dn] / h[d0] - 1) * 100
        entry[f"r{n}"] = round(r, 2)
        j0 = bisect.bisect_right(bdates, d0) - 1
        jn = bisect.bisect_right(bdates, dn) - 1
        if j0 >= 0 and jn >= 0 and b[bdates[j0]]:
            br = (b[bdates[jn]] / b[bdates[j0]] - 1) * 100
            entry[f"x{n}"] = round(r - br, 2)
    entry["base_date"] = d0


def is_hit(entry, n):
    x = entry.get(f"x{n}")
    if x is None or entry["effect"] == "watch":
        return None
    return x > 0 if entry["effect"] == "up" else x < 0


def summarize(log):
    def block(entries):
        out = {"total": len(entries), "news": len({(e["news_title"], e["date"]) for e in entries})}
        for n in HORIZONS:
            hits = [is_hit(e, n) for e in entries]
            hits = [h for h in hits if h is not None]
            xs = [e[f"x{n}"] * (1 if e["effect"] == "up" else -1) for e in entries
                  if e.get(f"x{n}") is not None and e["effect"] != "watch"]
            out[f"d{n}"] = {
                "evaluated": len(hits),
                "hit_rate": round(sum(hits) / len(hits) * 100, 1) if hits else None,
                "avg_edge": round(sum(xs) / len(xs), 2) if xs else None,  # ชนะดัชนีเฉลี่ย % ตามทิศที่ทาย
            }
        out["events"] = event_block(entries)
        return out

    entries = list(log.values())
    by_sig = {}
    for e in entries:
        by_sig.setdefault(e["signal"], {"name": e["signal_name"], "entries": []})["entries"].append(e)
    since = min((e["date"] for e in entries), default=None)
    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "since": since,
        "by_year": {y: event_block([e for e in entries if e["date"][:4] == y])["d5"] for y in sorted({e["date"][:4] for e in entries})},
        "backfill": sum(1 for e in entries if e.get("backfill")),
        "overall": block(entries),
        "tickers": {tk: block([e for e in entries if e["ticker"] == tk])["d5"] for tk in sorted({e["ticker"] for e in entries})},
        "signals": sorted(
            [dict(block(v["entries"]), id=k, name=v["name"]) for k, v in by_sig.items()],
            key=lambda x: -x["total"],
        ),
    }


def main():
    rules = load(ROOT / "config" / "rules.json", {"signals": []})
    news = load(ROOT / "docs" / "news.json", {})
    history = load(ROOT / "data" / "price_history.json", {})
    log_path = ROOT / "data" / "signal_log.json"
    log = load(log_path, {})
    removed = prune(log, news, rules)
    added = add_entries(log, news, rules)
    for e in log.values():
        evaluate(e, history)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(json.dumps(dict(sorted(log.items())), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    stats = summarize(log)
    (ROOT / "docs" / "stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    ev = stats["overall"]["events"]["d5"]
    print(f"สัญญาณใหม่ {added}, ลบตามกฎใหม่ {removed}, รวม {len(log)}, "
          f"ประเมิน 5 วันได้ {stats['overall']['d5']['evaluated']} (รายเหตุการณ์ {ev['evaluated']})", file=sys.stderr)


if __name__ == "__main__":
    main()
