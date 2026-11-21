"""频控与安全护栏。所有区间 [下限, 上限] 秒，每次随机取值。核心原则——宁可慢，不能把号搞没。"""
from loguru import logger

from boss_browser.human import sleep_range


class RateLimiter:
    """节奏控制（2026-11-21 按用户要求放慢）：

    - 打招呼:      最快 60s 一个   → 60~70s 随机
    - 查看详情:    最快 30s 一个   → 30~40s 随机
    - 翻页/切标签: 最快 5 分钟一次 → 300~420s 随机
    区间可在 config.yaml 的 rate 段调整。
    """

    def __init__(self, rate_cfg: dict):
        r = rate_cfg or {}
        self.greet = r.get("between_greets", [60, 70])
        self.page = r.get("between_pages", [300, 420])

    def wait_greet(self):
        sleep_range(*self.greet, "打招呼间隔")

    def wait_page(self):
        sleep_range(*self.page, "翻页/切标签间隔")


class DailyCap:
    """每日打招呼上限（SQLite 计数，跨运行生效）。"""

    def __init__(self, limit: int, db):
        self.limit = limit
        self.db = db

    def used(self) -> int:
        return self.db.greeted_today()

    def remaining(self) -> int:
        return max(0, self.limit - self.used())

    def allow(self) -> bool:
        if self.remaining() <= 0:
            logger.warning(f"已达每日打招呼上限 {self.limit}，今日收工")
            return False
        return True
