"""ทดสอบกฎกรอง/จับคู่ข่าว (ออฟไลน์ ไม่ต้องต่อเน็ต)  รัน: python tests/test_rules.py
หัวข้อข่าวในไฟล์นี้เป็นข้อความสมมุติสำหรับทดสอบกฎเท่านั้น ไม่ใช่ข่าวจริง"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import fetch_news as fn  # noqa: E402
import fetch_prices as fp  # noqa: E402

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


class PriceTests(unittest.TestCase):
    def chart(self, price, closes):
        return {"chart": {"result": [{"meta": {"regularMarketPrice": price, "currency": "THB"},
                                      "indicators": {"quote": [{"close": closes}]}}]}}

    def test_after_close(self):
        # ปิดตลาดแล้ว: ปิดล่าสุด = ราคาปัจจุบัน เทียบกับวันก่อนหน้า
        p = fp.parse_chart(self.chart(100.0, [95.0, None, 98.0, 100.0]))
        self.assertEqual(p["price"], 100.0)
        self.assertEqual(p["change_pct"], 2.04)

    def test_intraday(self):
        # ระหว่างวัน: ยังไม่มีปิดวันนี้ เทียบกับปิดล่าสุด
        self.assertEqual(fp.parse_chart(self.chart(101.0, [98.0, 100.0]))["change_pct"], 1.0)

    def test_missing(self):
        self.assertIsNone(fp.parse_chart({"chart": {"result": None}}))

    def test_set_suffix(self):
        t = fp.tickers_from_rules()
        self.assertEqual(t["PTT"]["symbol"], "PTT.BK")
        self.assertEqual(t["NVDA"]["symbol"], "NVDA")


if __name__ == "__main__":
    unittest.main(verbosity=1)
