"""期望职位推荐页导航 + API 数据捕获（2026-11 实测）。

页面结构：
- 「猜你要找」标签: div.expect-list > a.expect-item（每个期望一个）
- 点击标签 → 前端请求 /wapi/zpgeek/pc/recommend/job/list.json
  ?page=1&pageSize=15&encryptExpectId=...
- 接口返回明文职位数据（salaryDesc 等），完全绕过页面上的字体加密
- hasMore 表示还有下一页（页面向下滚动会再请求 page=2…）

策略：监听接口拿数据（准、快、明文），DOM 只用于点击标签等交互。
"""
import re

from loguru import logger

from .human import human_scroll, sleep_range
from .risk import check_risk

EXPECT_URL = "https://www.zhipin.com/web/geek/jobs"
JOB_LIST_API = "/wapi/zpgeek/pc/recommend/job/list.json"

# 实测有效
SELECTORS = {
    "expect_tabs": "css:div.expect-list a.expect-item",
}


class ExpectNavigator:
    def __init__(self, tab, cfg: dict):
        self.tab = tab
        self.cfg = cfg
        self._listening = False

    def open(self):
        """打开期望职位页并开始监听（首页默认标签的数据在打开时就会请求）。"""
        self.tab.listen.start(JOB_LIST_API)
        self._listening = True
        self.tab.get(EXPECT_URL)
        check_risk(self.tab)
        sleep_range(3, 5, "等待页面渲染")

    def expect_tabs(self) -> list:
        """页面上的期望职位标签元素。"""
        tabs = self.tab.eles(SELECTORS["expect_tabs"])
        names = [(t.text or "").split("\n")[0] for t in tabs]
        logger.info(f"获取到 {len(tabs)} 个期望职位标签: {names}")
        return tabs

    def _drain(self, timeout: float = 0.5) -> list:
        """清空监听队列（丢弃包）。"""
        try:
            return list(self.tab.listen.steps(timeout=timeout))
        except Exception:
            return []

    def click_tab_by_index(self, index: int) -> bool:
        """重新查询并点击第 index 个期望标签（触发列表数据请求）。"""
        try:
            tabs = self.tab.eles(SELECTORS["expect_tabs"])
            if len(tabs) > index:
                name = (tabs[index].text or "").split("\n")[0]
                logger.info(f"点击期望标签 [{index}] {name}")
                tabs[index].click()
                sleep_range(2.5, 4.5, "等待列表加载")
                return True
        except Exception as e:
            logger.debug(f"点击期望标签失败: {e}")
        return False

    def capture_jobs(self, timeout: int = 8) -> tuple:
        """从监听队列取一包当前列表数据（列表刷新/点击标签都会触发该接口）。

        返回 (jobList, 页号, hasMore)；超时没有新数据返回 ([], 0, False)。
        """
        for packet in self.tab.listen.steps(timeout=timeout):
            try:
                zp = (packet.response.body or {}).get("zpData") or {}
                jobs = zp.get("jobList") or []
            except Exception:
                continue
            if jobs:
                m = re.search(r"[?&]page=(\d+)", packet.url or "")
                page_no = int(m.group(1)) if m else 1
                logger.info(f"捕获当前列表: {len(jobs)} 个职位 "
                            f"(第{page_no}页, hasMore={zp.get('hasMore')})")
                return jobs, page_no, bool(zp.get("hasMore"))
        return [], 0, False

    def load_next_page(self, last_page: int) -> tuple:
        """按翻页节奏（最快5分钟一次）滚动加载下一页。

        返回 (jobs, 页号, hasMore)；若收到的是旧页缓存则 jobs 为空。
        """
        lo, hi = (self.cfg.get("rate") or {}).get("between_pages", [300, 420])
        sleep_range(lo, hi, "翻页间隔")
        human_scroll(self.tab, times=4, pause=(1.5, 2.5))
        jobs, page_no, has_more = self.capture_jobs(timeout=8)
        if jobs and page_no <= last_page:
            logger.info(f"收到第{page_no}页（非新页），视为没有更多")
            return [], page_no, has_more
        return jobs, page_no, has_more

    def iter_jobs_of_tab(self, tab_el, click: bool = True, max_pages: int = 1):
        """逐页产出当前期望标签下的职位（原始 dict 生成器）。

        click=False 用于首个标签（页面打开时已加载数据，直接收包即可）。
        翻页间隔按 rate.between_pages 控制（最快5分钟一次）——生成器模式让
        打招呼处理与翻页等待时间重叠，不干等。
        """
        tab_name = (tab_el.text or "").split("\n")[0]
        if click:
            self._drain()                   # 清掉上一轮残留（首屏包只在 click 模式下清）
            tab_el.click()
            sleep_range(2.5, 4.5, "等待列表加载")

        page = 0
        for packet in self.tab.listen.steps(timeout=10):
            try:
                zp = (packet.response.body or {}).get("zpData") or {}
                jobs = zp.get("jobList") or []
            except Exception:
                continue
            if not jobs:
                continue
            page += 1
            logger.info(f"「{tab_name}」第 {page} 页: {len(jobs)} 个职位 "
                        f"(hasMore={zp.get('hasMore')})")
            seen = set()
            for j in jobs:
                jid = j.get("encryptJobId")
                if jid and jid not in seen:
                    seen.add(jid)
                    yield j
            if page >= max_pages or not zp.get("hasMore"):
                break
            # 翻页节奏：最快5分钟一次（等待期间上层已在处理第1页的职位）
            lo, hi = (self.cfg.get("rate") or {}).get("between_pages", [300, 420])
            sleep_range(lo, hi, "翻页间隔")
            human_scroll(self.tab, times=4, pause=(1.5, 2.5))   # 触发下一页请求
        if page == 0:
            logger.warning(f"「{tab_name}」没有捕获到职位数据")

    def close(self):
        if self._listening:
            try:
                self.tab.listen.stop()
            except Exception:
                pass
            self._listening = False
