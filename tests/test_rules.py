"""ทดสอบกฎกรอง/จับคู่ข่าว (ออฟไลน์ ไม่ต้องต่อเน็ต)  รัน: python tests/test_rules.py
หัวข้อข่าวในไฟล์นี้เป็นข้อความสมมุติสำหรับทดสอบกฎเท่านั้น ไม่ใช่ข่าวจริง"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import fetch_news as fn  # noqa: E402
import fetch_prices as fp  # noqa: E402
import track_signals as ts  # noqa: E402
import validate_rules as vr  # noqa: E402
import fetch_calendar as fc  # noqa: E402

RULES = fn.load_json(ROOT / "config" / "rules.json")
CATS, SIGS = fn.build_rules(RULES)
ST = RULES["settings"]


def sigs(title, summary=""):
    return set(fn.classify(title, summary, CATS, SIGS, ST)[2])


def score(title, summary="", cb=False):
    return fn.classify(title, summary, CATS, SIGS, ST, cb)[0]


class SignalTests(unittest.TestCase):
    def test_signals_positive(self):
        cases = {
            "oil_up": ["ราคาน้ำมันดิบพุ่งแรง หลังโอเปกลดกำลังผลิต", "Oil prices jump as OPEC cuts output", "Brent crude climbs above $90"],
            "oil_down": ["น้ำมันดิบร่วงแรง กังวลอุปทานล้น", "Oil prices fall on demand worries", "Crude slides to two-month low"],
            "rate_cut": ["กนง. ปรับลดอัตราดอกเบี้ยนโยบาย 0.25%", "Fed cuts interest rates by a quarter point", "Markets bet on a rate cut in December"],
            "rate_hike": ["ธนาคารกลางขึ้นดอกเบี้ยเป็นครั้งที่ 3", "ECB raises interest rates again", "Traders fear another rate hike"],
            "gold_up": ["ราคาทองคำพุ่งทำนิวไฮใหม่", "Gold rallies to record as dollar slips"],
            "baht_weak": ["เงินบาทอ่อนค่าลงแตะ 35 บาทต่อดอลลาร์", "ค่าบาทอ่อนตามดอลลาร์แข็ง", "Baht weakens past 35 per dollar"],
            "baht_strong": ["เงินบาทแข็งค่าที่สุดในรอบปี", "Thai baht strengthens on inflows"],
            "tariff": ["สหรัฐขึ้นภาษีนำเข้าสินค้าจีน", "Trump threatens new tariffs on imports"],
            "tech_rally": ["Nvidia shares surge to record high", "ชิปพุ่งรับดีมานด์ AI"],
        }
        for sid, titles in cases.items():
            for t in titles:
                with self.subTest(signal=sid, title=t):
                    self.assertIn(sid, sigs(t))

    def test_signals_negative(self):
        cases = [
            ("rate_cut", "กนง. ไม่ลดดอกเบี้ย คงที่ 2.25%"),
            ("oil_up", "บริษัทตั้งโรงงานผลิตน้ำมันพืช ขยายตลาด"),
            ("oil_up", "The turmoil rises as boil advisory expands"),
            ("gold_up", "AURA กำไร Q3/69 โต พอร์ตขายฝาก “ทองมาเงินไป” นิวไฮ"),
            ("baht_weak", "เงินบาทแข็งค่า ผู้ส่งออกกังวล"),
            ("baht_strong", "เงินบาทอ่อนค่าลง"),
        ]
        for sid, t in cases:
            with self.subTest(signal=sid, title=t):
                self.assertNotIn(sid, sigs(t))

    def test_oil_inventory_is_not_price(self):
        # สต็อกน้ำมันเพิ่ม/ลด ไม่ใช่ราคาน้ำมันขึ้น/ลง
        self.assertEqual(sigs("EIA เผยสต็อกน้ำมันดิบสหรัฐเพิ่มขึ้นมากกว่าคาด") & {"oil_up", "oil_down"}, set())
        self.assertEqual(sigs("ราคาน้ำมันโลกดีดตัว ขานรับคาดการณ์สต็อกน้ำมันดิบสหรัฐลดลง") & {"oil_up", "oil_down"}, {"oil_up"})
        self.assertEqual(sigs("Oil prices climb 2% on drop in US crude inventories") & {"oil_up", "oil_down"}, {"oil_up"})

    def test_opposite_signals_pick_closest(self):
        self.assertEqual(sigs("Oil falls and Asian stocks rise on hopes of talks") & {"oil_up", "oil_down"}, {"oil_down"})
        self.assertEqual(sigs("Dollar climbs as crude prices jump and stocks fall") & {"oil_up", "oil_down"}, {"oil_up"})

    def test_summary_needs_related_title(self):
        # เนื้อข่าวเอ่ยถึงภาษีปี 2025 แต่หัวข้อเป็นเรื่องอื่น -> ไม่นับ
        self.assertNotIn("tariff", sigs("Anthropic's stock surges in its debut", "the worst day since the liberation day tariffs of April 2025"))
        # หัวข้อเป็นเรื่องค่าเงิน เนื้อข่าวบอกบาทอ่อน -> นับ
        self.assertIn("baht_weak", sigs("ค่าเงินบาทปิดตลาดที่ 34.01 บาท/ดอลลาร์", "เงินบาทอ่อนค่าลงต่อเนื่อง"))


class FilterTests(unittest.TestCase):
    def test_noise_is_dropped(self):
        noise = [
            "If you could change one thing about caregiving, what would it be?",
            "SpaceX is about to attempt the 'holy grail' of rocketry",
            "Federal Reserve Board announces pricing for payment services the Federal Reserve Banks provide",
            "Agencies extend comment period for proposed third-party risk management guidance",
            "ฝนตกหนักภาคใต้ เตือนน้ำท่วมฉับพลัน",
            "‘ชัชชาติ’ เร่งจัดการขยะ เปิดปฎิบัติการ “สงครามสู้หนู” ย่านสีลม เยาวราช",
            "ญี่ปุ่นเร่งเสริมกำลังทหาร เล็งเพิ่มงบกลาโหม 5 ปีแตะ 60 ล้านล้านเยน",
            "From recognition to integration: the ECB’s progress on climate and nature-related risks",
        ]
        for t in noise:
            with self.subTest(title=t):
                self.assertLess(score(t, cb=True), ST["min_score"])

    def test_relevant_is_kept(self):
        good = [
            "Federal Reserve announces revised tentative schedule of Federal Open Market Committee meetings in 2027",
            "KBANK คาดกรอบบาทสัปดาห์หน้า จับตา GDP ไทย",
            "SPRC กำไร Q3 แตะ 308 ล้านดอลลาร์ รับอานิสงส์สต็อกน้ำมัน",
            "Fed's Powell says inflation still too high",
            "ตลาดหุ้นไทยปิดบวก ดัชนี SET ขึ้น 10 จุด",
            "อิหร่านเผยเรือบรรทุกน้ำมัน 4 ลำชนทุ่นระเบิดใกล้ฮอร์มุซ UN เตือนกระทบการค้าโลก",
            "ECB monetary policy decisions",
            "เงินเยนอ่อนค่าหนัก ญี่ปุ่นเตรียมแทรกแซง",
        ]
        for t in good:
            with self.subTest(title=t):
                self.assertGreaterEqual(score(t, cb=True), ST["min_score"])

    def test_dollar_amount_is_not_fx(self):
        cats = fn.classify("บริษัทเซ็นสัญญา 300 ล้านดอลลาร์", "", CATS, SIGS, ST)[1]
        self.assertNotIn("fx", cats)

    def test_word_boundaries(self):
        # "oil" ต้องเป็นคำเต็ม ไม่จับ "boil" / "turmoil"
        cats = fn.classify("Chefs boil pasta amid turmoil", "", CATS, SIGS, ST)[1]
        self.assertNotIn("oil_energy", cats)


class DedupTests(unittest.TestCase):
    def test_dedup_similar_titles(self):
        items = [
            {"title": "Fed cuts rates by a quarter point", "score": 8, "published": "2026-11-13T10:00:00Z"},
            {"title": "Fed cuts rates by a quarter-point!", "score": 5, "published": "2026-11-13T11:00:00Z"},
            {"title": "ตลาดหุ้นไทยปิดบวก", "score": 4, "published": "2026-11-13T12:00:00Z"},
        ]
        out = fn.dedup(items, ST["dedup_similarity"])
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["score"], 8)


class StoryTests(unittest.TestCase):
    ST = fn.build_stories(fn.load_json(ROOT / "config" / "stories.json"))

    def tags(self, title, signals=()):
        return set(fn.tag_stories({"title": title, "summary": "", "signals": list(signals)}, self.ST))

    def test_story_tagging(self):
        self.assertIn("trade-war", self.tags("‘ไทย-สหรัฐ’ เจรจาภาษี 17 พ.ย."))
        self.assertIn("bot", self.tags("ผู้ว่าฯ ธปท. โต้ปมวิจารณ์การทำงาน"))
        self.assertIn("oil-mideast", self.tags("อิหร่านเผยเรือบรรทุกน้ำมันชนทุ่นระเบิดใกล้ฮอร์มุซ"))
        self.assertIn("ai-chips", self.tags("Nvidia’s stock is on a losing streak"))
        self.assertIn("baht", self.tags("ข่าวทั่วไป", ["baht_strong"]))  # จากสัญญาณ

    def test_story_negative(self):
        self.assertEqual(self.tags("ฝนตกหนักภาคใต้"), set())
        self.assertNotIn("gold", self.tags("Golden State wins"))


class PriceTests(unittest.TestCase):
    def chart(self, price, closes):
        return {"chart": {"result": [{"meta": {"regularMarketPrice": price, "currency": "THB"},
                                      "indicators": {"quote": [{"close": closes}]}}]}}

    def test_after_close(self):
        # ปิดตลาดแล้ว: ปิดล่าสุด = ราคาปัจจุบัน เทียบกับวันก่อนหน้า
        p = fp.parse_chart(self.chart(100.0, [95.0, None, 98.0, 100.0]))
        self.assertEqual(p["price"], 100.0)
        self.assertEqual(p["change_pct"], 2.04)

    def test_after_close_float_noise(self):
        # Yahoo ส่งราคาปิดมีเศษทศนิยมต่างจากราคาตอนนี้นิดเดียว ต้องไม่กลายเป็น 0%
        p = fp.parse_chart(self.chart(162.85, [160.0, 162.850006]))
        self.assertEqual(p["change_pct"], 1.78)

    def test_intraday(self):
        # ระหว่างวัน: ยังไม่มีปิดวันนี้ เทียบกับปิดล่าสุด
        self.assertEqual(fp.parse_chart(self.chart(101.0, [98.0, 100.0]))["change_pct"], 1.0)

    def test_prev_close_from_meta(self):
        d = {"chart": {"result": [{"meta": {"regularMarketPrice": 101.0, "chartPreviousClose": 100.0}, "indicators": {"quote": [{"close": [101.0]}]}}]}}
        self.assertEqual(fp.parse_chart(d)["change_pct"], 1.0)

    def test_missing(self):
        self.assertIsNone(fp.parse_chart({"chart": {"result": None}}))

    def test_set_suffix(self):
        t = fp.tickers_from_rules()
        self.assertEqual(t["PTT"]["symbol"], "PTT.BK")
        self.assertEqual(t["NVDA"]["symbol"], "NVDA")


class TrackTests(unittest.TestCase):
    HIST = {
        "CPF": {"2026-11-10": 20.0, "2026-11-11": 19.0, "2026-11-12": 18.0},
        "^SET": {"2026-11-10": 1000.0, "2026-11-11": 1010.0, "2026-11-12": 1000.0},
    }

    def entry(self, effect):
        return {"ticker": "CPF", "market": "SET", "effect": effect, "date": "2026-11-11"}

    def test_base_is_close_before_news_day(self):
        e = self.entry("down")
        ts.evaluate(e, self.HIST)
        self.assertEqual(e["base_date"], "2026-11-10")
        self.assertEqual(e["r1"], -5.0)       # 20 -> 19
        self.assertEqual(e["x1"], -6.0)       # แพ้ดัชนีที่ขึ้น 1%
        self.assertNotIn("r5", e)             # ยังไม่ครบ 5 วันทำการ

    def test_hit_uses_benchmark(self):
        down, up = self.entry("down"), self.entry("up")
        ts.evaluate(down, self.HIST); ts.evaluate(up, self.HIST)
        self.assertTrue(ts.is_hit(down, 1))
        self.assertFalse(ts.is_hit(up, 1))
        w = self.entry("watch"); ts.evaluate(w, self.HIST)
        self.assertIsNone(ts.is_hit(w, 1))

    def test_dedup_same_day(self):
        rules = {"signals": [{"id": "s", "name": "S", "impacts": [{"effect": "ถูกกดดัน", "set": ["CPF"], "us": []}]}]}
        news = {"items": [
            {"title": "a", "link": "x", "published": "2026-11-11T02:00:00Z", "signals": ["s"]},
            {"title": "b", "link": "y", "published": "2026-11-11T08:00:00Z", "signals": ["s"]},
        ]}
        log = {}
        self.assertEqual(ts.add_entries(log, news, rules), 1)

    def test_event_level_counts_once_per_day(self):
        # 1 ข่าว 3 หุ้น = 1 เหตุการณ์ ผลคือค่าเฉลี่ยของ 3 หุ้นตามทิศที่ทาย
        es = [{"signal": "s", "date": "2026-11-11", "effect": "up", "x1": v} for v in (2.0, -0.5, -0.5)]
        d1 = ts.event_block(es)["d1"]
        self.assertEqual(d1["evaluated"], 1)
        self.assertEqual(d1["hit_rate"], 100.0)
        self.assertEqual(d1["avg_edge"], 0.33)

    def test_event_level_no_overlap(self):
        # สัญญาณเดียวกันติดกันทุกวัน ช่วงวัด 5 วันซ้อนกัน -> นับเฉพาะที่ห่างกันอย่างน้อย 7 วัน
        es = [{"signal": "s", "date": f"2026-11-{d:02d}", "effect": "down", "x5": -1.0} for d in range(1, 15)]
        self.assertEqual(ts.event_block(es)["d5"]["evaluated"], 2)   # วันที่ 1 และ 8

    def test_prune_backfill_with_new_rules(self):
        log = {
            "oil_up|XOM|2026-01-01": {"signal": "oil_up", "ticker": "XOM", "date": "2026-01-01", "backfill": True,
                                      "news_title": "Crude inventories rise more than expected", "news_link": "a"},
            "oil_up|XOM|2026-01-02": {"signal": "oil_up", "ticker": "XOM", "date": "2026-01-02", "backfill": True,
                                      "news_title": "Oil prices jump as OPEC cuts output", "news_link": "b"},
            "oil_up|XOM|2026-01-03": {"signal": "oil_up", "ticker": "XOM", "date": "2026-01-03",
                                      "news_title": "x", "news_link": "c"},
        }
        news = {"items": [{"link": "c", "signals": []}]}
        self.assertEqual(ts.prune(log, news, RULES), 2)
        self.assertEqual(list(log), ["oil_up|XOM|2026-01-02"])

    def test_thai_date(self):
        # 20:00 UTC = 03:00 ของวันถัดไปตามเวลาไทย
        self.assertEqual(ts.news_date("2026-11-11T20:00:00Z"), "2026-11-12")


class ValidateTests(unittest.TestCase):
    def _hist(self, n_events, stock_wins):
        """น้ำมันขึ้น 3% ทุก 10 วัน; หุ้นพลังงานชนะ/แพ้ดัชนีหลังจากนั้นตาม stock_wins"""
        from datetime import date, timedelta
        oil, bench, stk = {}, {}, {}
        o, b, s = 100.0, 100.0, 100.0
        d0 = date(2020, 1, 1)
        for i in range(n_events * 10 + 10):
            d = (d0 + timedelta(days=i)).isoformat()
            if i % 10 == 5:
                o *= 1.03
            elif i % 10 in (6, 7):
                s *= 1.01 if stock_wins else 0.99
            oil[d], bench[d], stk[d] = o, b, s
        return {"M:OIL": oil, "^GSPC": bench, "XOM": stk}

    def _rules(self):
        return {"signals": [{"id": "oil_up", "name": "น้ำมันขึ้น", "impacts": [
            {"effect": "ได้ประโยชน์", "sector": "พลังงาน", "set": [], "us": ["XOM"]}]}]}

    def test_wilson(self):
        lo, hi = vr.wilson(70, 100)
        self.assertTrue(0.6 < lo < 0.7 < hi < 0.8)

    def test_confirmed(self):
        r = vr.validate(self._rules(), self._hist(40, True))[0]
        self.assertEqual(r["status"], "confirmed")
        self.assertEqual(r["hit_after"], 100.0)

    def test_contrary(self):
        r = vr.validate(self._rules(), self._hist(40, False))[0]
        self.assertEqual(r["status"], "contrary")

    def test_too_few_is_unclear(self):
        r = vr.validate(self._rules(), self._hist(10, True))[0]
        self.assertEqual(r["status"], "unclear")

    def test_no_proxy(self):
        rules = {"signals": [{"id": "tariff", "name": "ภาษี", "impacts": [{"effect": "ถูกกดดัน", "sector": "ส่งออก", "us": ["XOM"]}]}]}
        r = vr.validate(rules, {})[0]
        self.assertEqual(r["status"], "unclear")
        self.assertIn("note", r)


class CalendarTests(unittest.TestCase):
    CFG = fn.load_json(ROOT / "config" / "calendar_rules.json")
    RULES = fc.build_rules(CFG)

    def ev(self, title, country="USD", impact="High", date="2026-11-25T08:30:00-05:00"):
        return {"title": title, "country": country, "date": date, "impact": impact, "forecast": "0.3%", "previous": "0.2%"}

    def rule_of(self, title, country="USD"):
        r = fc.match_rule({"title": title, "country": country}, self.RULES)
        return r and r["id"]

    def test_rule_matching(self):
        cases = {
            "Core CPI m/m": "us_inflation", "Core PCE Price Index m/m": "us_inflation", "PPI m/m": "us_inflation",
            "Non-Farm Employment Change": "us_jobs", "Unemployment Claims": "us_jobless", "Unemployment Rate": "us_jobless",
            "Advance GDP q/q": "us_growth", "Retail Sales m/m": "us_growth", "ISM Services PMI": "us_growth",
            "Federal Funds Rate": "fed_rate", "FOMC Statement": "fed_talk", "FOMC Meeting Minutes": "fed_talk",
        }
        for t, rid in cases.items():
            with self.subTest(title=t):
                self.assertEqual(self.rule_of(t), rid)
        self.assertEqual(self.rule_of("Manufacturing PMI", "CNY"), "cn_data")
        self.assertEqual(self.rule_of("BOJ Policy Rate", "JPY"), "boj_rate")
        self.assertIsNone(self.rule_of("Prelim GDP Price Index q/q"))   # ตัวเลขรอง ไม่ใช่ GDP

    def test_filter_and_thai_relevance(self):
        raw = [self.ev("Core PCE Price Index m/m"), self.ev("CPI y/y", country="AUD"),
               self.ev("Pending Home Sales m/m", impact="Medium"), self.ev("Flash Services PMI", impact="Medium"),
               self.ev("Bank Holiday", impact="Holiday")]
        out = fc.normalize(raw, self.RULES, self.CFG)
        self.assertEqual([e["title"] for e in out], ["Core PCE Price Index m/m", "Flash Services PMI"])
        self.assertEqual(out[0]["time"], "2026-11-25T13:30:00Z")

    def test_base_close_respects_market_hours(self):
        dates = ["2026-11-24", "2026-11-25", "2026-11-26"]
        t = fc.parse_utc("2026-11-25T13:30:00Z")              # 08:30 นิวยอร์ก / 20:30 ไทย
        ny = next(a for a in fc.ASSETS if a["key"] == "SPX")
        th = next(a for a in fc.ASSETS if a["key"] == "SET")
        self.assertEqual(dates[fc.base_index(dates, t, ny)], "2026-11-24")   # ก่อนตลาดสหรัฐเปิด ใช้ราคาปิดวันก่อน
        self.assertEqual(dates[fc.base_index(dates, t, th)], "2026-11-25")   # หลังตลาดไทยปิด ใช้ราคาปิดวันนั้น

    def test_reaction(self):
        hist = {"^GSPC": {"2026-11-24": 100.0, "2026-11-25": 102.0, "2026-11-26": 101.0, "2026-11-27": 99.0},
                "M:US10Y": {"2026-11-24": 4.0, "2026-11-25": 4.1}}
        r = fc.reaction(fc.parse_utc("2026-11-25T13:30:00Z"), hist, fc.parse_utc("2026-12-01T00:00:00Z"))
        self.assertEqual(r["SPX"], {"d1": 2.0, "d3": -1.0})
        self.assertEqual(r["US10Y"], {"d1": 0.1})
        self.assertNotIn("GOLD", r)

    def test_build_from_sample(self):
        raw = json.loads((ROOT / "tests" / "samples" / "ff_calendar.json").read_text(encoding="utf-8"))
        hist = fc.merge_history({}, fc.normalize(raw, self.RULES, self.CFG), fc.parse_utc("2026-11-24T00:00:00Z"))
        news = {"items": [{"title": "สหรัฐเผยดัชนี PCE +3.8% สอดคล้องคาดการณ์", "link": "x", "source": "s", "published": "2026-11-25T15:15:58Z"},
                          {"title": "ข่าวอื่นไม่เกี่ยว", "link": "y", "published": "2026-11-25T15:00:00Z"}]}
        up, rc = fc.build(hist, self.RULES, news, {}, fc.parse_utc("2026-11-26T00:00:00Z"))
        self.assertEqual([e["country"] for e in up], ["USD", "CNY"])           # FOMC แล้วตามด้วย PMI จีน
        self.assertEqual(rc[0]["items"][0]["title"], "Core PCE Price Index m/m")
        self.assertEqual({r["id"] for r in rc[0]["rules"]}, {"us_inflation", "us_growth"})   # PCE + GDP ออกพร้อมกัน
        self.assertEqual([n["link"] for n in rc[0]["news"]], ["x"])

if __name__ == "__main__":
    unittest.main(verbosity=1)
