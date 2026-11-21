"""数据库层 P0 修复测试：mark_job upsert / is_done applications 回落 / update_job_desc upsert。

跑法：python tests/test_db.py
复现第二版反馈的验证场景：jobs 表为空时去重依然有效。
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from storage.db import Database
from storage.models import Application, Job


class TestDedupFallback(unittest.TestCase):
    def setUp(self):
        self.db = Database(Path(tempfile.mkdtemp()) / "t.db")

    def test_mark_job_upsert_on_missing_row(self):
        """jobs 行不存在时 mark_job 必须写入（P0-1 核心）。"""
        jid = "newid123"
        self.assertFalse(self.db.is_done(jid))
        self.db.mark_job(jid, "greeted")       # 此前是 UPDATE，命中 0 行静默失败
        self.assertTrue(self.db.is_done(jid))

    def test_is_done_falls_back_to_applications(self):
        """jobs 表从未落库：仅 applications 有 greeted 记录也要拦住。"""
        jid = "appid456"
        self.db.record(Application(jid, "职位", "公司", status="greeted"))
        self.assertTrue(self.db.is_done(jid))  # 回落查询生效

    def test_is_done_ignores_failed_and_dry_run(self):
        jid = "softid789"
        self.db.record(Application(jid, "职位", "公司", status="failed"))
        self.db.record(Application(jid, "职位", "公司", status="dry_run"))
        self.assertFalse(self.db.is_done(jid))  # 失败/演练不阻断重试

    def test_update_job_desc_upsert(self):
        """jobs 行不存在时 update_job_desc 也必须写入（P0-2）。"""
        j = Job(job_id="descid000", title="AI负责人", salary="40-60K")
        j.job_desc = "岗位职责……"
        self.db.update_job_desc(j)
        row = self.db.conn.execute(
            "SELECT raw_json FROM jobs WHERE job_id='descid000'").fetchone()
        self.assertIsNotNone(row)
        self.assertIn("岗位职责", row["raw_json"])

    def test_save_job_preserves_status(self):
        """save_job 是 INSERT OR IGNORE：不覆盖已有终态。"""
        jid = "keepid111"
        self.db.mark_job(jid, "greeted")
        self.db.save_job(Job(job_id=jid, title="X"))
        self.assertTrue(self.db.is_done(jid))


if __name__ == "__main__":
    unittest.main(verbosity=2)
