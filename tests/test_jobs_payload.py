"""jobs 载荷冒烟测试（R26-1 回归）：不启浏览器，直接喂原始数据。

覆盖：字段映射 / 薪资折算 / 方向信号 / 硬过滤标注 / 落库 / 去重标注。
跑法：python tests/test_jobs_payload.py
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from cli import build_jobs_payload
from storage.db import Database
from storage.models import Application

RAW = {
    "encryptJobId": "abc123",
    "jobName": "AI Agent 平台负责人",
    "salaryDesc": "50-80K·15薪",
    "brandName": "某科技公司",
    "brandScaleName": "500-999人", "brandStageName": "B轮", "brandIndustry": "人工智能",
    "bossName": "王女士", "bossTitle": "HR",
    "skills": ["大模型", "Agent"], "jobLabels": ["5-10年", "本科"],
    "cityName": "北京", "areaDistrict": "朝阳区", "businessDistrict": "望京",
    "jobExperience": "5-10年", "jobDegree": "本科",
}

CFG = {"filter": {"allowed_cities": ["北京"], "salary_band_k": [30, 60],
                  "blacklist_keywords": []}}


class TestBuildJobsPayload(unittest.TestCase):
    def setUp(self):
        self.db = Database(Path(tempfile.mkdtemp()) / "t.db")

    def tearDown(self):
        self.db.conn.close()

    def test_fields_mapped(self):
        out = build_jobs_payload([RAW], ["AI 技术总监"], CFG, self.db)
        self.assertEqual(len(out), 1)
        j = out[0]
        self.assertEqual(j["job_id"], "abc123")
        self.assertEqual(j["title"], "AI Agent 平台负责人")
        self.assertEqual(j["salary"], "50-80K·15薪")
        self.assertEqual(j["salary_months"], 15)
        self.assertIsNotNone(j["effective_monthly_k"])
        self.assertEqual(j["hr"], "王女士（HR）")
        self.assertIn("北京", j["location"])

    def test_saved_to_db(self):
        build_jobs_payload([RAW], ["AI 技术总监"], CFG, self.db)
        row = self.db.get_job("abc123")
        self.assertEqual(row.get("hr_name"), "王女士")     # R25-4/R26-2 的依赖
        self.assertEqual(row.get("title"), "AI Agent 平台负责人")

    def test_filter_flag(self):
        low = dict(RAW, encryptJobId="low1", salaryDesc="10-15K")
        out = build_jobs_payload([low], ["AI"], CFG, self.db)
        self.assertFalse(out[0]["filter_ok"])

    def test_direction_fields(self):
        out = build_jobs_payload([RAW], ["AI 技术总监"], CFG, self.db)
        j = out[0]
        self.assertIn("ai", j["direction_keywords"])          # 标题命中
        self.assertIn("ai", j["direction_match"])             # 与画像交集
        self.assertFalse(j["no_direction_signal"])
        self.assertIn("大模型", j["direction_keywords_tags"])   # 标签命中

    def test_processed_flag(self):
        self.db.record(Application("abc123", "x", "y", status="greeted"))
        out = build_jobs_payload([RAW], ["AI"], CFG, self.db)
        self.assertTrue(out[0]["processed"])

    def test_empty_input(self):
        self.assertEqual(build_jobs_payload([], ["AI"], CFG, self.db), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
