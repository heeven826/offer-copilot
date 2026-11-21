"""纯函数单元测试：英文判定/对象识别。跑法：python tests/test_greeting.py"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from ai.greeting import is_english_text, detect_audience
from storage.models import Job

EN_JD = ("We are looking for an experienced AI Engineering Director to lead our "
         "LLM application team. You will be responsible for team management and "
         "delivery of enterprise AI solutions. Requirements: 8+ years experience.")
CN_JD = "岗位职责：负责公司大模型应用的技术架构与团队管理，主导RAG与Agent产品落地。"


class TestEnglishDetect(unittest.TestCase):
    def test_en(self):
        self.assertTrue(is_english_text(EN_JD))

    def test_cn(self):
        self.assertFalse(is_english_text(CN_JD))

    def test_short(self):
        self.assertFalse(is_english_text("hello world"))
        self.assertFalse(is_english_text(""))


class TestAudience(unittest.TestCase):
    def test_business(self):
        job = Job(hr_title="HR经理", hr_name="张女士")
        self.assertEqual(detect_audience(job), "business")

    def test_tech(self):
        job = Job(hr_title="技术总监", hr_name="王先生")
        self.assertEqual(detect_audience(job), "tech")

    def test_boss(self):
        job = Job(hr_title="创始人", hr_name="李总")
        self.assertEqual(detect_audience(job), "business")


class TestPersona(unittest.TestCase):
    def test_from_profile(self):
        from ai.greeting import _persona
        name, years = _persona({"basics": {"name": "张三", "years_of_experience": 8}})
        self.assertEqual((name, years), ("张三", "8"))

    def test_fallback(self):
        from ai.greeting import _persona
        name, years = _persona({})
        self.assertEqual(name, "候选人")


if __name__ == "__main__":
    unittest.main(verbosity=2)
