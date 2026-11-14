#!/usr/bin/env python3
"""กรองเศรษฐกิจ: ดึงข่าวจาก RSS -> กรอง/จัดหมวด -> จับคู่สัญญาณกับหุ้น -> เขียน docs/news.json

ไม่ใช้ AI และไม่ใช้ token: ทุกอย่างเป็นกฎใน config/rules.json

ใช้งาน:
    python scripts/fetch_news.py                       # ดึงจริงจาก config/feeds.json
    python scripts/fetch_news.py --from-dir tests/samples   # ทดสอบแบบออฟไลน์ (อ่านไฟล์ <feed id>.xml)
"""
import argparse
import difflib
import hashlib
import html
import json
import re
import socket
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import feedparser

ROOT = Path(__file__).resolve().parent.parent
UA = "KrongSetthakit/0.1 (personal news filter)"
TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")
NORM_RE = re.compile(r"[^0-9a-zA-Z฀-๿]+")


def log(msg):
    print(msg, file=sys.stderr)


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------- rules
def build_rules(rules):
    """คอมไพล์ keyword และ pattern จาก rules.json"""
    cats = []
    for c in rules["categories"]:
        kws = []
        for k in c["keywords"]:
            case_sensitive = k.startswith("cs:")
            raw = k[3:] if case_sensitive else k
            if raw.isascii():
                # ภาษาอังกฤษ: ต้องเป็นคำเต็ม (รับ s/es ท้ายคำ) กันชน เช่น "oil" ใน "boil"
                pat = r"(?<![A-Za-z0-9])" + re.escape(raw) + r"(?:s|es)?(?![A-Za-z0-9])"
            else:
                pat = re.escape(raw)
            kws.append(re.compile(pat, 0 if case_sensitive else re.IGNORECASE))
        cats.append({"id": c["id"], "name": c["name"], "kws": kws})
    sigs = []
    for s in rules["signals"]:
        sigs.append({
            "id": s["id"],
            "name": s["name"],
            "patterns": [re.compile(p, re.IGNORECASE | re.DOTALL) for p in s["patterns"]],
            "impacts": s["impacts"],
        })
    return cats, sigs


def classify(title, summary, cats, sigs, settings, central_bank=False):
    """คืน (score, category_ids, signal_ids)"""
    score = 0
    scored_cats = []
    for c in cats:
        n_title = sum(1 for rx in c["kws"] if rx.search(title))
        n_sum = sum(1 for rx in c["kws"] if rx.search(summary))
        pts = 0
        if n_title:
            pts += 3 + (1 if n_title >= 2 else 0)
        pts += min(2, n_sum)
        if pts:
            score += pts
        if pts >= 2:
            scored_cats.append((pts, c["id"]))
    text = title + " " + summary
    sig_ids = [s["id"] for s in sigs if any(p.search(text) for p in s["patterns"])]
    score += settings["signal_bonus"] * len(sig_ids)
    if score > 0 and central_bank:
        score += settings["central_bank_bonus"]
    scored_cats.sort(key=lambda x: -x[0])
    return score, [cid for _, cid in scored_cats[:3]], sig_ids


# ---------------------------------------------------------------- fetching
def clean_text(s, limit=None):
    s = html.unescape(s or "")
    s = TAG_RE.sub(" ", s)
    s = SPACE_RE.sub(" ", s).strip()
    if limit and len(s) > limit:
        s = s[: limit - 1].rstrip() + "…"
    return s


def entry_time(e, fallback):
    for k in ("published_parsed", "updated_parsed"):
        t = e.get(k)
        if t:
            return datetime(*t[:6], tzinfo=timezone.utc)
    return fallback


def parse_feed(feed, from_dir):
    if from_dir:
        path = Path(from_dir) / (feed["id"] + ".xml")
        if not path.exists():
            raise FileNotFoundError(f"ไม่มีไฟล์ตัวอย่าง {path}")
        return feedparser.parse(path.read_bytes())
    return feedparser.parse(
        feed["url"],
        agent=UA,
        request_headers={"Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*"},
    )


def collect(feeds, from_dir, now, summary_max):
    items, errors, ok = {}, [], 0
    for feed in feeds:
        if not feed.get("enabled", True):
            continue
        try:
            d = parse_feed(feed, from_dir)
            status = d.get("status")
            if status and status >= 400:
                raise RuntimeError(f"HTTP {status}")
            if not d.entries:
                raise RuntimeError("ไม่พบรายการข่าว" + (f" ({d.bozo_exception})" if d.get("bozo") else ""))
        except Exception as exc:  # noqa: BLE001 - ข้ามแหล่งที่พัง แต่ไม่ให้ทั้งรอบล้ม
            errors.append({"feed": feed["id"], "error": str(exc)[:200]})
            log(f"[ผิดพลาด] {feed['id']}: {exc}")
            continue
        ok += 1
        for e in d.entries:
            link = (e.get("link") or "").strip()
            title = clean_text(e.get("title"))
            if not link or not title:
                continue
            iid = hashlib.sha1(link.encode("utf-8")).hexdigest()[:12]
            items[iid] = {
                "id": iid,
                "title": title,
                "link": link,
                "source": feed["name"],
                "feed": feed["id"],
                "region": feed["region"],
                "lang": feed["lang"],
                "central_bank": bool(feed.get("central_bank")),
                "published": entry_time(e, now).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "summary": clean_text(e.get("summary") or e.get("description"), summary_max),
            }
        log(f"[ok] {feed['id']}: {len(d.entries)} รายการ")
    return items, errors, ok


# ---------------------------------------------------------------- pipeline
def norm_title(t):
    return NORM_RE.sub("", t.lower())


def dedup(items, threshold):
    """ตัดข่าวหัวข้อซ้ำ/คล้ายกันมาก เก็บอันที่คะแนนสูงสุด"""
    kept, norms = [], []
    for it in sorted(items, key=lambda x: (-x["score"], x["published"])):
        n = norm_title(it["title"])
        dup = False
        for m in norms[-300:]:
            sm = difflib.SequenceMatcher(None, n, m)
            if sm.real_quick_ratio() >= threshold and sm.quick_ratio() >= threshold and sm.ratio() >= threshold:
                dup = True
                break
        if not dup:
            kept.append(it)
            norms.append(n)
    return kept


def build_signal_summary(items, sigs, window_hours, now):
    cutoff = (now - timedelta(hours=window_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    by_sig = {s["id"]: s for s in sigs}
    buckets = {}
    for it in items:
        if it["published"] < cutoff:
            continue
        for sid in it["signals"]:
            buckets.setdefault(sid, []).append(it)
    out = []
    for sid, lst in buckets.items():
        lst.sort(key=lambda x: x["published"], reverse=True)
        s = by_sig[sid]
        out.append({
            "id": sid,
            "name": s["name"],
            "count": len(lst),
            "impacts": s["impacts"],
            "evidence": [
                {"title": x["title"], "link": x["link"], "source": x["source"], "published": x["published"]}
                for x in lst[:3]
            ],
        })
    out.sort(key=lambda x: -x["count"])
    return out


def run(args):
    feeds = load_json(ROOT / "config" / "feeds.json")["feeds"]
    rules = load_json(ROOT / "config" / "rules.json")
    st = rules["settings"]
    cats, sigs = build_rules(rules)
    out_path = Path(args.out) if args.out else ROOT / "docs" / "news.json"
    socket.setdefaulttimeout(25)
    now = datetime.now(timezone.utc)

    raw, errors, ok = collect(feeds, args.from_dir, now, st["summary_max_chars"])
    if ok == 0:
        log("ดึงข่าวไม่ได้เลยสักแหล่ง: ไม่เขียนทับไฟล์เดิม")
        return 1

    # รวมกับข่าวเก่า (เก็บประวัติ max_age_days วัน) แล้วคิดคะแนนใหม่ทั้งหมดด้วยกฎล่าสุด
    old_items = {}
    if out_path.exists() and not args.no_merge:
        try:
            for it in load_json(out_path).get("items", []):
                old_items[it["id"]] = it
        except Exception as exc:  # noqa: BLE001
            log(f"อ่านไฟล์เก่าไม่ได้ ข้าม: {exc}")
    merged = {**old_items, **raw}

    age_cutoff = (now - timedelta(days=st["max_age_days"])).strftime("%Y-%m-%dT%H:%M:%SZ")
    kept = []
    for it in merged.values():
        if it["published"] < age_cutoff:
            continue
        score, cat_ids, sig_ids = classify(
            it["title"], it.get("summary", ""), cats, sigs, st, it.get("central_bank", False)
        )
        if score < st["min_score"]:
            continue
        it = dict(it, score=score, categories=cat_ids, signals=sig_ids)
        kept.append(it)

    kept = dedup(kept, st["dedup_similarity"])
    kept.sort(key=lambda x: x["published"], reverse=True)
    kept = kept[: st["max_items"]]

    data = {
        "meta": {
            "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "categories": {c["id"]: c["name"] for c in cats},
            "feeds_ok": ok,
            "feed_errors": errors,
            "total_raw": len(raw),
            "total_kept": len(kept),
        },
        "signals": build_signal_summary(kept, sigs, st["signal_window_hours"], now),
        "items": kept,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    log(f"เขียน {out_path} : เก็บ {len(kept)} จาก {len(raw)} รายการ, สัญญาณ {len(data['signals'])}")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from-dir", help="อ่าน RSS จากไฟล์ <feed id>.xml ในโฟลเดอร์นี้แทนการดึงจริง")
    ap.add_argument("--out", help="ไฟล์ผลลัพธ์ (ค่าเริ่มต้น docs/news.json)")
    ap.add_argument("--no-merge", action="store_true", help="ไม่รวมกับ news.json เดิม")
    sys.exit(run(ap.parse_args()))


if __name__ == "__main__":
    main()
