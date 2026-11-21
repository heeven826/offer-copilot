"""REQ-10 健康指标单测：失败率/成功率/截断率/周漂移/汇总告警。
跑法：python tests/test_health.py
"""
import sys
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from health import (fail_rate, jd_success_rate, truncate_rate, score_drift,
                    summarize)
from storage.db import Database


def ev(outcome, **kw):
    import json
    d = {"outcome": outcome, "detail": json.dumps(kw)}
    return d


class TestRates(unittest.TestCase):
    def test_fail_rate(self):
        events = [ev("ok")] * 15 + [ev("fail")] * 5
        r = fail_rate(events, window=20)
        self.assertEqual(r["value"], 0.25)
        self.assertEqual(r["samples"], 20)

    def test_fail_rate_window(self):
        events = [ev("fail")] * 10 + [ev("ok")] * 10     # 旧的10次失败滑出窗口
        r = fail_rate(events, window=10)
        self.assertEqual(r["value"], 0.0)

    def test_jd_rate(self):
        events = [ev("ok", jd_len=500)] * 7 + [ev("ok", jd_len=0)] * 3
        self.assertEqual(jd_success_rate(events)["value"], 0.7)

    def test_truncate_rate(self):
        events = [ev("ok", truncated=True)] * 2 + [ev("ok")] * 8
        self.assertEqual(truncate_rate(events)["value"], 0.2)


class TestDrift(unittest.TestCase):
    def _rows(self, days_ago, score, n):
        d = (date.today() - timedelta(days=days_ago)).isoformat()
        return [{"score": score, "created_at": d}] * n

    def test_drift_computed(self):
        rows = self._rows(1, 8, 12) + self._rows(8, 5, 12)   # 本周8 上周5
        sd = score_drift(rows)
        self.assertEqual(sd["drift"], 3.0)
        self.assertTrue(sd["judgeable"])

    def test_baseline_not_judgeable(self):
        rows = self._rows(1, 8, 3) + self._rows(8, 5, 3)     # 样本不足
        sd = score_drift(rows)
        self.assertFalse(sd["judgeable"])
        self.assertIsNone(sd["drift"])
        self.assertIn("还差", sd["insufficient"])             # R14 note：明示差多少

    def test_zero_scores_excluded(self):
        """R14-1：score=0（未评分）不得混入均值——防'评分崩塌'假警报。"""
        rows = self._rows(1, 8, 12) + self._rows(8, 5, 12)
        zeros = [{"score": 0, "created_at": (date.today() - timedelta(days=1)).isoformat()}] * 20
        sd = score_drift(rows + zeros)
        self.assertEqual(sd["this_week"]["samples"], 12)      # 0分被排除
        self.assertEqual(sd["unscored_excluded"], 20)
        self.assertEqual(sd["drift"], 3.0)                    # 均值不被拉低


class TestSummarize(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.db_path = Path(tempfile.mkdtemp()) / "t.db"
        self.db = Database(self.db_path)
        self.cfg = {"health": {"send_fail_rate": 0.35, "risk_events_24h": 2,
                               "detail_jd_rate": 0.7, "msg_truncate_rate": 0.2}}

    def tearDown(self):
        self.db.conn.close()   # Windows 文件锁：先关连接再清理
        try:
            self.db_path.unlink()
        except OSError:
            pass

    def test_healthy(self):
        self.db.audit("greet", "j1", "ok", msg_len=80)
        s = summarize(self.db, self.cfg)
        self.assertEqual(s["status"], "healthy")
        self.assertEqual(s["warnings"], [])

    def test_send_fail_warning(self):
        for i in range(5):
            self.db.audit("greet", f"j{i}", "fail", stage="greet_button")
        s = summarize(self.db, self.cfg)
        self.assertEqual(s["status"], "warning")
        self.assertEqual(s["warnings"][0]["code"], "SEND_FAIL_HIGH")

    def test_risk_warning(self):
        now = datetime.now().isoformat(timespec="seconds")
        for i in range(2):
            self.db.conn.execute(
                "INSERT INTO audit (ts,action,target,outcome,detail) VALUES (?,?,?,?,?)",
                (now, "risk", "", "warn", "{}"))
        self.db.conn.commit()
        s = summarize(self.db, self.cfg)
        self.assertTrue(any(w["code"] == "RISK_FREQUENT" for w in s["warnings"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
