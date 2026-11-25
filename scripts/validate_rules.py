#!/usr/bin/env python3
"""ทดสอบกฎใน config/rules.json กับราคาจริงย้อนหลัง (ไม่ต้องใช้ข่าว) -> docs/validation.json

แนวคิด: แต่ละสัญญาณมี "ตัวแทนราคา" ที่วัดได้จริง เช่น น้ำมันขึ้น = วันที่น้ำมัน WTI ขึ้นเกิน 2%
ทุกวันที่เกิดเหตุการณ์แบบนั้นในข้อมูลย้อนหลัง ดูว่าหุ้นในกลุ่มที่กฎบอก ชนะ/แพ้ดัชนีตลาดตามที่ทายไหม

วัด 2 แบบ:
- "หลังข่าว" (after): จากราคาปิดวันที่เกิดเหตุ (หุ้นไทย: วันทำการถัดไป) ถึง 5 วันทำการถัดไป = สิ่งที่คนอ่านข่าวแล้วลงมือยังทันได้
- "ทั้งสัปดาห์" (week): จากราคาปิดก่อนวันเกิดเหตุ ถึง 5 วันทำการถัดไป = รวมการตอบสนองวันแรก

สถานะของแต่ละกฎ (สัญญาณ x กลุ่มหุ้น) ตัดสินจาก "หลังข่าว" และเหตุการณ์อย่างน้อย 30 ครั้ง:
- confirmed: ช่วงความเชื่อมั่น 90% ของอัตราถูกทาง อยู่เหนือ 50% ทั้งช่วง
- contrary: อยู่ใต้ 50% ทั้งช่วง (ข้อมูลบอกว่ากฎทายกลับทาง)
- unclear: คร่อม 50% หรือข้อมูลไม่พอ
"""
import bisect
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HORIZON = 5
MIN_EVENTS = 30
BENCH = {"SET": "^SET", "US": "^GSPC"}

# ตัวแทนราคาของแต่ละสัญญาณ: (ชุดราคาใน price_history, ชนิดการเปลี่ยน, เกณฑ์)
#   pct = % เปลี่ยนรายวัน, abs = เปลี่ยนเป็นหน่วย (บอนด์ยีลด์ ^TNX เป็น % อยู่แล้ว)
PROXY = {
    "oil_up": ("M:OIL", "pct", 2.0, "วันที่น้ำมัน WTI ขึ้นเกิน 2%"),
    "oil_down": ("M:OIL", "pct", -2.0, "วันที่น้ำมัน WTI ลงเกิน 2%"),
    "gold_up": ("M:GOLD", "pct", 1.5, "วันที่ทองคำขึ้นเกิน 1.5%"),
    "rate_hike": ("M:US10Y", "abs", 0.08, "วันที่บอนด์ยีลด์สหรัฐ 10 ปี ขึ้นเกิน 0.08%"),
    "rate_cut": ("M:US10Y", "abs", -0.08, "วันที่บอนด์ยีลด์สหรัฐ 10 ปี ลงเกิน 0.08%"),
    "baht_weak": ("M:USDTHB", "pct", 0.5, "วันที่ดอลลาร์/บาท ขึ้นเกิน 0.5% (บาทอ่อน)"),
    "baht_strong": ("M:USDTHB", "pct", -0.5, "วันที่ดอลลาร์/บาท ลงเกิน 0.5% (บาทแข็ง)"),
    "tech_rally": ("M:NDX", "pct", 2.0, "วันที่ Nasdaq ขึ้นเกิน 2%"),
}


def load(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default


def wilson(k, n, z=1.645):
    """ช่วงความเชื่อมั่น ~90% ของสัดส่วน k/n"""
    if n == 0:
        return 0.0, 1.0
    p = k / n
    den = 1 + z * z / n
    c = p + z * z / (2 * n)
    r = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (c - r) / den, (c + r) / den


def events(series, kind, thr):
    """วันที่เกิดเหตุการณ์ตามเกณฑ์ (ต้องมีราคาวันก่อนหน้าในชุดเดียวกัน)"""
    ds = sorted(series)
    out = []
    for a, b in zip(ds, ds[1:]):
        pa, pb = series[a], series[b]
        if not pa:
            continue
        ch = (pb / pa - 1) * 100 if kind == "pct" else pb - pa
        if (thr > 0 and ch >= thr) or (thr < 0 and ch <= thr):
            out.append(b)
    return out


def excess(h, b, d_from, d_to):
    """ผลตอบแทนหุ้นลบดัชนี (%) จากปิดวัน d_from ถึงปิดวัน d_to (ใช้ราคาล่าสุดที่ไม่เกินวันนั้น)"""
    def at(series, dates, d):
        i = bisect.bisect_right(dates, d) - 1
        return series[dates[i]] if i >= 0 else None
    hd, bd = sorted(h), sorted(b)
    h0, h1, b0, b1 = at(h, hd, d_from), at(h, hd, d_to), at(b, bd, d_from), at(b, bd, d_to)
    if None in (h0, h1, b0, b1) or not h0 or not b0:
        return None
    return (h1 / h0 - 1) * 100 - (b1 / b0 - 1) * 100


def validate(rules, history):
    out = []
    for sig in rules["signals"]:
        px = PROXY.get(sig["id"])
        for im in sig["impacts"]:
            effect = im["effect"]
            rec = {"signal": sig["id"], "signal_name": sig["name"], "sector": im["sector"], "effect": effect,
                   "proxy": px[3] if px else None, "status": "unclear", "events": 0}
            if not px or effect == "ต้องจับตา" or px[0] not in history:
                rec["note"] = "ยังไม่มีราคาตัวแทน รอผลจากข่าวย้อนหลัง" if not px else "ไม่ทายทิศทาง" if effect == "ต้องจับตา" else "ยังไม่มีราคาย้อนหลัง"
                out.append(rec)
                continue
            sgn = 1 if effect == "ได้ประโยชน์" else -1
            ev = events(history[px[0]], px[1], px[2])
            after, week, n_days = [], [], 0
            for d in ev:
                # ต่อหนึ่งวันเหตุการณ์: เฉลี่ยทุกหุ้นในกลุ่ม แล้วนับเป็น 1 ครั้ง (หุ้นในกลุ่มเดียวกันไม่อิสระต่อกัน)
                a_vals, w_vals = [], []
                for market, tks in (("SET", im.get("set", [])), ("US", im.get("us", []))):
                    b = history.get(BENCH[market]) or {}
                    for tk in tks:
                        h = history.get(tk) or {}
                        hd = sorted(h)
                        # ราคาตัวแทน (น้ำมัน ทอง บอนด์ Nasdaq) ปิดตอนตลาดสหรัฐปิด = กลางคืนเวลาไทย
                        # หุ้นไทยจึงเริ่มนับจากราคาปิดวันถัดไป (คนอ่านข่าวตอนเช้าซื้อได้เร็วสุดวันนั้น)
                        i = bisect.bisect_right(hd, d) - (0 if market == "SET" else 1)
                        if i < 1 or i + HORIZON >= len(hd):
                            continue
                        d_prev, d0, d5 = hd[i - 1], hd[i], hd[i + HORIZON]
                        x_after = excess(h, b, d0, d5)
                        x_week = excess(h, b, d_prev, d5)
                        if x_after is not None:
                            a_vals.append(sgn * x_after)
                        if x_week is not None:
                            w_vals.append(sgn * x_week)
                if a_vals:
                    after.append(sum(a_vals) / len(a_vals))
                    week.append(sum(w_vals) / len(w_vals) if w_vals else 0)
                    n_days += 1
            n = len(after)
            k = sum(1 for x in after if x > 0)
            kw = sum(1 for x in week if x > 0)
            lo, hi = wilson(k, n)
            status = "unclear"
            if n >= MIN_EVENTS and lo > 0.5:
                status = "confirmed"
            elif n >= MIN_EVENTS and hi < 0.5:
                status = "contrary"
            rec.update({
                "events": n,
                "since": min(ev) if ev else None,
                "hit_after": round(k / n * 100, 1) if n else None,
                "edge_after": round(sum(after) / n, 2) if n else None,
                "hit_week": round(kw / n * 100, 1) if n else None,
                "ci": [round(lo * 100, 1), round(hi * 100, 1)],
                "status": status,
            })
            out.append(rec)
    return out


def main():
    rules = load(ROOT / "config" / "rules.json", {"signals": []})
    history = load(ROOT / "data" / "price_history.json", {})
    res = validate(rules, history)
    data = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "horizon_days": HORIZON,
        "min_events": MIN_EVENTS,
        "rules": res,
    }
    (ROOT / "docs" / "validation.json").write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    for r in res:
        print(f"{r['status']:10} {r['signal']:12} {r['sector'][:28]:30} n={r['events']:4} after={r.get('hit_after')} week={r.get('hit_week')} ci={r.get('ci')}", file=sys.stderr)


if __name__ == "__main__":
    main()
