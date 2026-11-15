#!/usr/bin/env python3
"""กรองเศรษฐกิจ: เก็บสถิติว่าสัญญาณที่ระบบบอก แม่นจริงไหม

ทุกครั้งที่รัน:
1. อ่านข่าวที่มีสัญญาณจาก docs/news.json แล้วบันทึกลง data/signal_log.json
   (หนึ่งรายการต่อ สัญญาณ x หุ้น x วัน ข่าวซ้ำวันเดียวกันนับครั้งเดียว)
2. เทียบราคาปิดก่อนวันข่าว กับราคาปิดหลังจากนั้น 1, 5, 20 วันทำการ (จาก data/price_history.json)
   และเทียบกับดัชนีตลาด (SET หรือ S&P 500) ในช่วงเดียวกัน
3. สรุปผลลง docs/stats.json ให้หน้าเว็บแสดง

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
        return out

    entries = list(log.values())
    by_sig = {}
    for e in entries:
        by_sig.setdefault(e["signal"], {"name": e["signal_name"], "entries": []})["entries"].append(e)
    since = min((e["date"] for e in entries), default=None)
    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "since": since,
        "by_year": {y: block([e for e in entries if e["date"][:4] == y])["d5"] for y in sorted({e["date"][:4] for e in entries})},
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
    added = add_entries(log, news, rules)
    for e in log.values():
        evaluate(e, history)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(json.dumps(dict(sorted(log.items())), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    stats = summarize(log)
    (ROOT / "docs" / "stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"สัญญาณใหม่ {added}, รวม {len(log)}, ประเมิน 5 วันได้ {stats['overall']['d5']['evaluated']}", file=sys.stderr)


if __name__ == "__main__":
    main()
