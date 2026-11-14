#!/usr/bin/env python3
"""กรองเศรษฐกิจ: ดึงราคาหุ้นที่อยู่ใน config/rules.json จาก Yahoo Finance -> docs/prices.json

- หุ้นไทยเติม .BK อัตโนมัติ (PTT -> PTT.BK)
- ไม่ต้องใช้คีย์ ไม่ใช้ token
- ถ้าดึงไม่ได้บางตัว จะเก็บราคาเดิมของตัวนั้นไว้ ถ้าดึงไม่ได้เลยจะไม่เขียนทับไฟล์เดิม
  และจบแบบไม่ error เพื่อไม่ให้การอัปเดตข่าวล้มตามไปด้วย

ใช้งาน:
    python scripts/fetch_prices.py
    python scripts/fetch_prices.py --from-dir tests/prices   # ทดสอบออฟไลน์ (อ่านไฟล์ <SYMBOL>.json)
"""
import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
URL = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=3mo&interval=1d"
BENCHMARKS = {"^SET": {"symbol": "^SET.BK", "market": "SET"}, "^GSPC": {"symbol": "^GSPC", "market": "US"}}
UA = "Mozilla/5.0 (KrongSetthakit personal news filter)"


def log(msg):
    print(msg, file=sys.stderr)


def tickers_from_rules():
    rules = json.loads((ROOT / "config" / "rules.json").read_text(encoding="utf-8"))
    out = {}
    for s in rules["signals"]:
        for im in s["impacts"]:
            for t in im.get("set", []):
                out[t] = {"symbol": t + ".BK", "market": "SET"}
            for t in im.get("us", []):
                out[t] = {"symbol": t, "market": "US"}
    out.update(BENCHMARKS)  # ดัชนีตลาด ไว้เทียบว่าหุ้นชนะตลาดไหม
    return out


def fetch_chart(symbol, from_dir=None):
    if from_dir:
        return json.loads((Path(from_dir) / (symbol + ".json")).read_text(encoding="utf-8"))
    req = urllib.request.Request(URL.format(sym=urllib.parse.quote(symbol)), headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def parse_chart(data):
    """คืน dict ราคา หรือ None ถ้าข้อมูลไม่ครบ"""
    res = (data.get("chart") or {}).get("result") or []
    if not res:
        return None
    r = res[0]
    meta = r.get("meta") or {}
    quote = ((r.get("indicators") or {}).get("quote") or [{}])[0]
    closes = [c for c in (quote.get("close") or []) if c is not None]
    price = meta.get("regularMarketPrice")
    if price is None and closes:
        price = closes[-1]
    if price is None:
        return None
    # ราคาปิดก่อนหน้า: ถ้าปิดล่าสุดเท่ากับราคาตอนนี้ (วันนี้ปิดแล้ว) ใช้ตัวก่อนหน้านั้น
    prev = None
    if len(closes) >= 2:
        prev = closes[-2] if abs(closes[-1] - price) <= max(1e-6, abs(price) * 1e-4) else closes[-1]
    elif meta.get("previousClose") is not None:
        prev = meta["previousClose"]
    change = round((price - prev) / prev * 100, 2) if prev else None
    ts = meta.get("regularMarketTime")
    return {
        "price": round(float(price), 2),
        "change_pct": change,
        "currency": meta.get("currency", ""),
        "asof": datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if ts else None,
    }


def parse_history(data):
    """ราคาปิดรายวัน {YYYY-MM-DD: close} ตามวันที่ของตลาดนั้น"""
    res = ((data.get("chart") or {}).get("result") or [None])[0] or {}
    ts = res.get("timestamp") or []
    closes = (((res.get("indicators") or {}).get("quote") or [{}])[0].get("close")) or []
    off = ((res.get("meta") or {}).get("gmtoffset")) or 0
    out = {}
    for t, c in zip(ts, closes):
        if c is not None:
            out[datetime.fromtimestamp(t + off, timezone.utc).strftime("%Y-%m-%d")] = round(float(c), 4)
    return out


def run(args):
    out_path = Path(args.out) if args.out else ROOT / "docs" / "prices.json"
    old = {}
    if out_path.exists():
        try:
            old = json.loads(out_path.read_text(encoding="utf-8")).get("prices", {})
        except Exception:  # noqa: BLE001
            pass
    hist_path = Path(args.history) if args.history else ROOT / "data" / "price_history.json"
    try:
        history = json.loads(hist_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        history = {}
    prices, errors = {}, []
    for name, info in tickers_from_rules().items():
        try:
            raw = fetch_chart(info["symbol"], args.from_dir)
            history.setdefault(name, {}).update(parse_history(raw))
            p = parse_chart(raw)
            if not p:
                raise ValueError("ไม่มีข้อมูลราคา")
            prices[name] = dict(p, market=info["market"], symbol=info["symbol"])
        except Exception as exc:  # noqa: BLE001
            errors.append(name)
            log(f"[ผิดพลาด] {name}: {str(exc)[:120]}")
            if name in old:
                prices[name] = dict(old[name], stale=True)
        if not args.from_dir:
            time.sleep(0.4)
    fresh = len([p for p in prices.values() if not p.get("stale")])
    if fresh == 0:
        log("ดึงราคาไม่ได้เลย: ไม่เขียนทับไฟล์เดิม")
        return 0
    data = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "Yahoo Finance",
        "errors": errors,
        "prices": prices,
    }
    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    hist_path.parent.mkdir(parents=True, exist_ok=True)
    history = {k: dict(sorted(v.items())[-400:]) for k, v in sorted(history.items())}  # เก็บไม่เกิน ~1.5 ปีต่อตัว
    hist_path.write_text(json.dumps(history, separators=(",", ":")) + "\n", encoding="utf-8")
    log(f"เขียน {out_path}: ราคาใหม่ {fresh} ตัว, ผิดพลาด {len(errors)} ตัว")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from-dir")
    ap.add_argument("--out")
    ap.add_argument("--history")
    sys.exit(run(ap.parse_args()))


if __name__ == "__main__":
    main()
