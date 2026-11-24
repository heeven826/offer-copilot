"""发送路径单测（R26-3）：send_and_verify 的 Enter 优先 / 按钮兜底 / 校验失败。

用假 tab 模拟聊天页，不联网、不启浏览器。跑法：python tests/test_send.py
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import boss_browser.actions as actions
from boss_browser.actions import Communicator


class FakeClick:
    """模拟 DrissionPage 的 click 代理（__call__ 与 by_offset[]）。"""
    def __init__(self, on_click=None):
        self.calls = 0
        self._on_click = on_click

    def by_offset(self, x, y):
        self.calls += 1
        if self._on_click:
            self._on_click()

    def __call__(self, *a, **k):
        self.calls += 1
        if self._on_click:
            self._on_click()


class FakeElem:
    def __init__(self, text="", cls="", on_click=None, on_input=None):
        self._text = text
        self._cls = cls
        self._value = ""
        self._on_input = on_input
        self.click = FakeClick(on_click)

    @property
    def text(self):
        return self._text

    def attr(self, name):
        if name == "class":
            return self._cls
        if name == "value":
            return self._value
        return None

    def input(self, text, by_js=False):
        self._text = text
        self._value = text
        if self._on_input:
            self._on_input(text)

    def ele(self, sel, timeout=None):
        if "text-content" in sel:
            return FakeElem(text=self._text)
        return None

    def clear(self):
        self._text = ""
        self._value = ""


class FakeTab:
    """最小聊天页：输入框 + 消息列表 + actions.type(ENTER) + 发送按钮。"""
    def __init__(self, enter_works=True, button_works=True):
        self.messages = []
        self.enter_works = enter_works
        self.button_works = button_works
        self.url = "https://www.zhipin.com/web/geek/chat"
        self.input_box = FakeElem(on_input=self._remember)
        self._pending = ""
        self.actions = self                       # actions.type(Keys.ENTER)
        self._send_btn = FakeElem(cls="btn-v2 btn-sure-v2 btn-send",
                                  on_click=self._button_send)

    # ---- 内部 ----
    def _remember(self, text):
        self._pending = text

    def _commit(self):
        if self._pending:
            self.messages.append(FakeElem(text=self._pending,
                                          cls="message-item item-myself"))
            self.input_box.clear()
            self._pending = ""

    def _button_send(self):
        if self.button_works:
            self._commit()

    # ---- 模拟 tab 接口 ----
    def type(self, key):                          # actions.type(Keys.ENTER)
        if self.enter_works:
            self._commit()

    def ele(self, sel, timeout=None):
        if isinstance(sel, list):
            for s in sel:
                e = self.ele(s, timeout)
                if e:
                    return e
            return None
        if "chat-input" in sel or "textarea" in sel:
            return self.input_box
        if "btn-send" in sel or "btn-sure-v2" in sel:
            return self._send_btn
        return None

    def eles(self, sel):
        if "message-item" in sel:
            return self.messages
        return []


class TestSendAndVerify(unittest.TestCase):
    def setUp(self):
        self._orig_sleep = actions.sleep_range
        actions.sleep_range = lambda *a, **k: None      # 测试不真等待

    def tearDown(self):
        actions.sleep_range = self._orig_sleep

    def _comm(self, tab):
        return Communicator(tab, {"rate": {}}, db=None)

    def test_enter_path(self):
        """Enter 生效 → verified=True、stage=done、消息落库。"""
        tab = FakeTab(enter_works=True)
        r = self._comm(tab).send_and_verify("你好，我是候选人")
        self.assertTrue(r["verified"])
        self.assertEqual(r["stage"], "done")
        self.assertEqual(len(tab.messages), 1)

    def test_button_fallback(self):
        """Enter 无效但按钮有效 → done_btn（按钮兜底生效）。"""
        tab = FakeTab(enter_works=False, button_works=True)
        r = self._comm(tab).send_and_verify("按钮兜底测试")
        self.assertTrue(r["verified"])
        self.assertEqual(r["stage"], "done_btn")

    def test_silent_failure_detected(self):
        """Enter 与按钮都无效 → verified=False（R24/R25 的静默失败必须被抓出）。"""
        tab = FakeTab(enter_works=False, button_works=False)
        r = self._comm(tab).send_and_verify("这条发不出去")
        self.assertFalse(r["verified"])
        self.assertEqual(r["stage"], "send_unverified")
        self.assertEqual(tab.messages, [])

    def test_no_input(self):
        class NoInputTab(FakeTab):
            def ele(self, sel, timeout=None):
                return None
        r = self._comm(NoInputTab()).send_and_verify("x")
        self.assertFalse(r["verified"])
        self.assertEqual(r["stage"], "chat_input")


if __name__ == "__main__":
    unittest.main(verbosity=2)
