"""纯函数单元测试：薪资解析/折算/硬过滤（含 null 上限）。跑法：python tests/test_filters.py"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from filters import hard_filter, parse_salary, effective_monthly
from storage.models import Job

BASE_CFG = {
    "filter": {
        "allowed_cities": ["北京"],
        "salary_band_k": [30, 60],
        "blacklist_keywords": [],
    }
}


class TestParseSalary(unittest.TestCase):
    def test_cases(self):
        self.assertEqual(parse_salary("30-50K·16薪"), (30.0, 16))
        self.assertEqual(parse_salary("80-130K"), (80.0, 12))
        self.assertEqual(parse_salary("300-500元/天"), (None, None))
        self.assertEqual(parse_salary("面议"), (None, None))
        self.assertEqual(parse_salary(""), (None, None))

    def test_effective(self):
        self.assertAlmostEqual(effective_monthly(40, 16), 53.33, places=1)
        self.assertEqual(effective_monthly(30, 12), 30.0)


class TestHardFilter(unittest.TestCase):
    def test_city(self):
        ok, why = hard_filter(Job(salary="40K", location="青岛"), BASE_CFG)
        self.assertFalse(ok); self.assertIn("城市", why)

    def test_city_remote_pass(self):
        ok, _ = hard_filter(Job(title="Remote AI Engineer", salary="40-60K",
                                location="深圳"), BASE_CFG)
        self.assertTrue(ok)

    def test_band_low(self):
        ok, why = hard_filter(Job(salary="25-40K", location="北京"), BASE_CFG)
        self.assertFalse(ok); self.assertIn("低于下限", why)

    def test_band_high(self):
        ok, why = hard_filter(Job(salary="80-130K", location="北京"), BASE_CFG)
        self.assertFalse(ok); self.assertIn("超预期", why)

    def test_band_high_null_top(self):
        """上限 null = 不限高（开源默认）。"""
        cfg = {"filter": dict(BASE_CFG["filter"], salary_band_k=[30, None])}
        ok, _ = hard_filter(Job(salary="80-130K", location="北京"), cfg)
        self.assertTrue(ok)

    def test_16_salary_folding(self):
        """16薪折算：30K×16薪=40K/月、40K×16薪=53.3K/月 都应通过 [30,60]。"""
        ok, _ = hard_filter(Job(salary="30-50K·16薪", location="北京"), BASE_CFG)
        self.assertTrue(ok)
        ok2, _ = hard_filter(Job(salary="40-70K·16薪", location="北京"), BASE_CFG)
        self.assertTrue(ok2)   # 折算 53.3 在带内
        ok3, _ = hard_filter(Job(salary="50-80K·13薪", location="北京"), BASE_CFG)
        self.assertTrue(ok3)   # 折算 54.2 在带内
        ok4, why4 = hard_filter(Job(salary="60-90K·13薪", location="北京"), BASE_CFG)
        self.assertFalse(ok4); self.assertIn("超预期", why4)   # 折算 65 超带

    def test_blacklist(self):
        cfg = {"filter": dict(BASE_CFG["filter"], blacklist_keywords=["外包"])}
        ok, why = hard_filter(Job(title="外包驻场开发", salary="40K",
                                  location="北京"), cfg)
        self.assertFalse(ok); self.assertIn("黑名单", why)

    def test_empty_location_pass(self):
        ok, _ = hard_filter(Job(salary="40-60K", location=""), BASE_CFG)
        self.assertTrue(ok)  # 地点未知不硬杀


if __name__ == "__main__":
    unittest.main(verbosity=2)
