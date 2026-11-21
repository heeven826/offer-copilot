"""沟通动作（2026-11-21 联调实测通过）。

完整流程（期望职位页 → 打招呼）：
1. 按 encryptJobId 定位列表卡片: a.job-name[href*=...], 点击展开页内详情面板
2. 点击「立即沟通」a.op-btn-chat → 同标签页跳转 /web/geek/chat
3. 在 div.chat-input（contenteditable）输入话术（点击不会自动发默认招呼语，已实测）
4. 点击 button.btn-send 发送（输入有内容后按钮才可用）
5. tab.back() 返回职位列表

拟人化：所有点击带随机偏移（不总是点正中心）、每步之间随机停顿
（模拟"浏览详情""检查话术"），配合 main 的随机间隔与偶发长休息。
"""
import random
import time

from loguru import logger

from .expect_page import SELECTORS as EXPECT_SELECTORS
from .human import sleep_range
from .risk import check_risk

# 2026-11-21 实测有效
GREET_READY = True

SELECTORS = {
    "job_link": "css:a.job-name",          # 卡片职位名链接（href 含 encryptJobId）
    # 「立即沟通」两套：期望页内嵌详情面板 / 独立详情页
    "greet_buttons": ["css:a.op-btn-chat", "css:a.btn-startchat"],
    # 职位描述两套：内嵌面板 / 独立详情页
    "job_descs": ["css:div.job-detail-body p.desc", "css:.job-sec-text"],
    "chat_input": "css:div.chat-input",    # 聊天输入框（contenteditable=true）
    "chat_send": "css:button.btn-send",    # 发送按钮
}


class Communicator:
    def __init__(self, tab, cfg: dict, db=None):
        self.tab = tab
        self.cfg = cfg
        self.db = db                  # 用于跨进程节奏状态持久化（CLI 模式必须传）
        self._expect_index = None    # 当前期望标签下标（列表刷新后用于重新定位）
        rate = cfg.get("rate") or {}
        self._detail_range = rate.get("between_details", [30, 40])

    def _get_ts(self, key: str) -> float:
        try:
            return float(self.db.get_state(key) or 0) if self.db else 0.0
        except Exception:
            return 0.0

    def _set_ts(self, key: str):
        if self.db:
            try:
                self.db.set_state(key, str(time.time()))
            except Exception:
                pass

    def _pace_details(self):
        """两次查看职位详情之间保持 30-40s（随机）节奏 —— 跨进程持久化。"""
        need = random.uniform(*self._detail_range)
        elapsed = time.time() - self._get_ts("last_detail_ts")
        if elapsed < need:
            wait = need - elapsed
            logger.debug(f"详情节奏等待 {wait:.0f}s")
            time.sleep(wait)
        self._set_ts("last_detail_ts")

    def set_expect_tab(self, index: int):
        """记住当前正在处理的期望标签下标。"""
        self._expect_index = index

    @staticmethod
    def _jitter_click(ele, xr: tuple, yr: tuple):
        """带随机偏移的点击 —— 避免每次都点元素正中心的机器特征。"""
        try:
            ele.click.by_offset(random.randint(*xr), random.randint(*yr))
        except Exception:
            try:
                ele.click()
            except Exception as e:
                raise RuntimeError(f"点击失败: {e}") from e

    def _find_card(self, eid: str):
        return self.tab.ele(
            f'xpath://a[@class="job-name" and contains(@href,"{eid}")]', timeout=3)

    def _reanchor_list(self):
        """返回列表后页面会刷新重排 —— 重新点击当前期望标签，重新载入列表。"""
        if self._expect_index is None:
            return
        try:
            tabs = self.tab.eles(EXPECT_SELECTORS["expect_tabs"])
            if tabs and len(tabs) > self._expect_index:
                logger.info(f"列表已刷新，重新载入期望标签 [{self._expect_index}]")
                self._jitter_click(tabs[self._expect_index], (-60, 60), (-4, 4))
                sleep_range(2.5, 4.5, "等待列表重载")
        except Exception as e:
            logger.debug(f"重载期望标签失败: {e}")

    def _first_ele(self, selectors: list, timeout: int = 3):
        """按顺序尝试多个选择器，返回第一个命中的元素。"""
        for s in selectors:
            e = self.tab.ele(s, timeout=timeout)
            if e:
                return e
        return None

    def read_job_desc(self) -> str:
        """读取当前详情页/面板的职位描述（兼容两套布局，清洗平台水印）。"""
        p = self._first_ele(SELECTORS["job_descs"], timeout=2)
        if not p:
            return ""
        text = p.text or ""
        for wm in ("BOSS直聘", "来自BOSS直聘"):
            text = text.replace(wm, "")
        return text.strip()

    def open_detail(self, job):
        """打开职位详情，返回职位描述文本；仅当无 job_id 时返回 False。

        三级兜底（推荐池每次刷新都会轮换内容，卡片经常消失）：
        1. 在当前列表里找到卡片 → 点击展开（最像真人）
        2. 重载当前期望标签后再找一次
        3. 直接打开详情页 URL（job_detail/{encryptJobId}.html，永远可用）
        """
        self._pace_details()
        eid = getattr(job, "job_id", "") or ""
        if not eid:
            logger.warning(f"职位无 encryptJobId: {job.brief()}")
            return False

        link = self._find_card(eid)
        if not link:
            self._reanchor_list()
            link = self._find_card(eid)
        if link:
            self._jitter_click(link, (-50, 50), (-3, 3))
            sleep_range(1.8, 4.2, "浏览职位详情")   # 模拟阅读详情
        else:
            url = f"https://www.zhipin.com/job_detail/{eid}.html"
            logger.info(f"列表中卡片已轮换，直接打开详情页: {job.brief()}")
            self.tab.get(url)
            sleep_range(2.5, 4.0, "等待详情页加载")

        jd = self.read_job_desc()
        if jd:
            logger.debug(f"已抓取职位描述 {len(jd)} 字")
        return jd

    def greet(self, greeting_text: str, job=None, detail_open: bool = False) -> bool:
        """发送打招呼话术。

        detail_open=True 表示详情面板已由 open_detail 打开（省一次定位），
        此时返回的 JD 已由调用方处理。
        """
        try:
            # 输入框 maxlength=300，超长先截断到句子边界
            if len(greeting_text) > 290:
                cut = max(greeting_text.rfind(". ", 0, 290),
                          greeting_text.rfind("。", 0, 290),
                          greeting_text.rfind("！", 0, 290))
                greeting_text = greeting_text[:cut + 1] if cut > 100 else greeting_text[:290]
                logger.warning(f"话术超长，已截断至 {len(greeting_text)} 字符")

            if job is not None and not detail_open:
                if self.open_detail(job) is False:
                    return False

            btn = self._first_ele(SELECTORS["greet_buttons"], timeout=5)
            if not btn:
                logger.warning("未找到「立即沟通」按钮（可能已在沟通中/职位已关闭）")
                self._back()
                return False
            self._jitter_click(btn, (-40, 40), (-8, 8))
            sleep_range(2, 3.5, "等待聊天页")
            check_risk(self.tab)

            inp = self.tab.ele(SELECTORS["chat_input"], timeout=6)
            if not inp:
                logger.error("未找到聊天输入框")
                self._back()
                return False
            self._jitter_click(inp, (-40, 40), (-3, 3))
            sleep_range(0.5, 1.2)
            inp.input(greeting_text)
            sleep_range(1.0, 2.8, "检查话术")   # 发送前的犹豫
            send = self.tab.ele(SELECTORS["chat_send"], timeout=3)
            if not send:
                logger.error("未找到发送按钮，已放弃本次发送")
                self._back()
                return False
            self._jitter_click(send, (-15, 15), (-4, 4))
            sleep_range(1.2, 2.5)
            logger.success(f"已发送打招呼: {greeting_text[:36]}…")
            self._set_ts("last_greet_ts")     # 跨进程消息节奏锚点
            self._back()
            return True
        except Exception as e:
            logger.error(f"打招呼失败: {e}")
            try:
                self._back()
            except Exception:
                pass
            return False

    def send_resume_card(self) -> bool:
        """聊天窗口发送简历卡片 —— 交互未实测，保持 TODO。"""
        logger.warning("发送简历卡片尚未实现（TODO）")
        return False

    def _back(self):
        """从聊天页返回职位列表。"""
        self.tab.back()
        sleep_range(1.5, 2.5, "返回职位列表")
