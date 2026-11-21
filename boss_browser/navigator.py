"""搜索导航：打开搜索结果页、遍历职位卡片、翻页。

所有选择器集中在 SELECTORS —— 页面改版时只改这里。
字段提取阶段我们会用 --probe 采样的真实 HTML 校准它们。
"""
from urllib.parse import quote

from loguru import logger

from .human import sleep_range
from .risk import check_risk

# TODO(字段提取): 以下选择器为占位，需对真实页面校准
SELECTORS = {
    "job_card": "css:.job-card-wrapper",   # 列表页单个职位卡片
    "next_page": "css:a.next",             # 下一页按钮
}


class JobListNavigator:
    def __init__(self, tab, cfg: dict):
        self.tab = tab
        self.cfg = cfg

    def open_search(self, keyword: str):
        """按关键词打开搜索结果页。"""
        city = self.cfg.get("search", {}).get("city", "100010000")
        url = f"https://www.zhipin.com/web/geek/job?query={quote(keyword)}&city={city}"
        logger.info(f"打开搜索页: {keyword} (city={city})")
        self.tab.get(url)
        check_risk(self.tab)
        sleep_range(2, 4, "等待搜索页加载")

    def job_cards(self):
        """迭代当前页的所有职位卡片元素。"""
        cards = self.tab.eles(SELECTORS["job_card"])
        logger.info(f"本页获取到 {len(cards)} 个职位卡片")
        yield from cards

    def next_page(self) -> bool:
        """翻到下一页，成功返回 True。"""
        btn = self.tab.ele(SELECTORS["next_page"], timeout=3)
        if not btn:
            logger.info("没有下一页了")
            return False
        btn.click()
        sleep_range(*self.cfg.get("rate", {}).get("between_pages", [3, 7]), "翻页间隔")
        check_risk(self.tab)
        return True
