"""身份校验测试（R30 P0）：发错人是本工具最不可接受的错误。

覆盖：会话头身份匹配（正确/HR不符/职位不符/同名不同岗/缺信号）、详情页归属、
发送互斥锁。跑法：python tests/test_identity.py
"""
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import cli
from boss_browser.actions import Communicator


class FakeElem:
    def __init__(self, text=""):
        self._text = text

    @property
    def text(self):
        return self._text


class HeaderTab:
    """只提供会话头的假 tab。"""
    def __init__(self, header=""):
        self.header = header

    def ele(self, sel, timeout=None):
        if "top-info" in str(sel) or "chat-top-info" in str(sel):
            return FakeElem(self.header) if self.header else None
        return None


class TestIdentity(unittest.TestCase):
    def _comm(self, header):
        return Communicator(HeaderTab(header), {"rate": {}}, db=None)

    def test_ok(self):
        c = self._comm("王女士 / HR / AI Agent 平台负责人 / 50-80K·15薪")
        self.assertTrue(c.identity_ok_name("王女士", "AI Agent 平台负责人"))

    def test_hr_mismatch(self):
        c = self._comm("李先生 / HR / AI Agent 平台负责人")
        self.assertFalse(c.identity_ok_name("王女士", "AI Agent 平台负责人"))

    def test_title_mismatch(self):
        """同名 HR 不同职位 → 拒绝（R30 的梁先生事故场景）。"""
        c = self._comm("王女士 / HR / 浏览器研发总监")
        self.assertFalse(c.identity_ok_name("王女士", "AI FDE"))

    def test_truncated_title_prefix(self):
        """会话头里职位名被截断时，前 6 字匹配应通过。"""
        c = self._comm("王女士 / HR / Personal Agent开发工程师(...)")
        self.assertTrue(c.identity_ok_name("王女士", "Personal Agent开发工程师(AI应用)"))

    def test_company_signal(self):
        c = self._comm("王女士 / 某科技公司 / AI 负责人")
        self.assertTrue(c.identity_ok_name("王女士", "完全不匹配的岗位", "某科技公司"))

    def test_empty_header_rejects(self):
        """读不到会话头 → 不发（宁可失败）。"""
        self.assertFalse(self._comm("").identity_ok_name("王女士", "任何职位"))

    def test_no_signal_rejects(self):
        """既没职位也没公司 → 无法校验 → 不发。"""
        self.assertFalse(self._comm("王女士 / HR").identity_ok_name("王女士", "", ""))


class TestDetailMatch(unittest.TestCase):
    class DetailTab:
        def __init__(self, name):
            self.name = name

        def ele(self, sel, timeout=None):
            if "name" in str(sel) or "h1" in str(sel):
                return FakeElem(self.name) if self.name else None
            return None

    def test_match(self):
        c = Communicator(self.DetailTab("AI Agent 平台负责人"), {"rate": {}}, db=None)

        class J:
            title = "AI Agent 平台负责人"
        self.assertTrue(c._detail_matches(J()))

    def test_mismatch(self):
        c = Communicator(self.DetailTab("浏览器研发总监"), {"rate": {}}, db=None)

        class J:
            title = "AI Agent 平台负责人"
        self.assertFalse(c._detail_matches(J()))   # R29-1：串页必须被识别


class TestSendLock(unittest.TestCase):
    def setUp(self):
        self._orig = cli._SEND_LOCK
        cli._SEND_LOCK = Path(tempfile.mkdtemp()) / ".send.lock"

    def tearDown(self):
        cli._release_send_lock()
        cli._SEND_LOCK = self._orig

    def test_second_acquire_refused(self):
        cli.acquire_send_lock()
        with self.assertRaises(SystemExit):
            cli.acquire_send_lock()

    def test_stale_lock_taken_over(self):
        import json
        cli._SEND_LOCK.parent.mkdir(parents=True, exist_ok=True)
        cli._SEND_LOCK.write_text(
            json.dumps({"pid": 1, "ts": time.time() - 3600}), encoding="utf-8")
        cli.acquire_send_lock()      # 超过 10 分钟自动失效，可接管
        self.assertTrue(cli._SEND_LOCK.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
