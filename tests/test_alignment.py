"""compute_alignment 测试：方向词白名单 + 停用词 + 缺侧 unknown。

跑法：python tests/test_alignment.py
核心回归用例：第二版反馈中 "AI 交付工程师"[-2:]="程师" 命中 "全栈工程师"
的假阳性必须为 False。
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from filters import compute_alignment

REAL_TABS = ["全栈工程师(北京)", "技术合伙人(北京)", "技术总监(北京)"]


class TestAlignment(unittest.TestCase):
    def test_no_false_positive_suffix(self):
        """reviewer 场景：FDE/AI交付/大模型应用 vs 三个通用标签 = 不对齐。"""
        roles = ["FDE / AI 交付工程师", "大模型应用专家"]
        r = compute_alignment(REAL_TABS, roles)
        self.assertFalse(r["aligned"])
        self.assertEqual(r["overlap"], [])
        self.assertIn("空转", r["suggestion"])

    def test_generic_title_words_not_evidence(self):
        """只有"技术总监"这类通用职称交集不算对齐。"""
        r = compute_alignment(["技术总监(北京)"], ["技术总监"])
        self.assertFalse(r["aligned"])

    def test_direction_keyword_match(self):
        """方向词（ai/大模型等）两侧都有才算对齐。"""
        r = compute_alignment(["AI应用开发(北京)"], ["AI 技术总监", "大模型应用"])
        self.assertTrue(r["aligned"])
        self.assertIn("ai", r["overlap"])
        self.assertTrue(r["evidence"])

    def test_case_insensitive(self):
        r = compute_alignment(["LLM平台(北京)"], ["大模型 LLM 工程师"])
        self.assertTrue(r["aligned"])

    def test_unknown_when_missing_side(self):
        """任一侧为空 → aligned=None（不要默认 True）。"""
        r = compute_alignment(REAL_TABS, [])
        self.assertIsNone(r["aligned"])
        self.assertIn("unknown", r["note"])
        r2 = compute_alignment([], ["AI 技术总监"])
        self.assertIsNone(r2["aligned"])

    def test_fullstack_direction(self):
        r = compute_alignment(["全栈工程师(北京)"], ["全栈开发"])
        self.assertTrue(r["aligned"])   # "全栈" 是方向词，双侧命中


if __name__ == "__main__":
    unittest.main(verbosity=2)
