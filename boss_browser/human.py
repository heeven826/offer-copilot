"""拟人化行为：随机延迟、模拟滚动，降低风控风险。"""
import random
import time

from loguru import logger


def sleep_range(a: float, b: float, label: str = "") -> None:
    """在 [a, b] 秒之间随机休眠。"""
    t = random.uniform(a, b)
    if label:
        logger.debug(f"等待 {t:.1f}s ({label})")
    time.sleep(t)


def human_scroll(tab, times: int = 3, pause: tuple = (0.5, 1.5)) -> None:
    """模拟人类滚动浏览页面。tab 为 DrissionPage 的 Tab 对象。"""
    for _ in range(times):
        dy = random.randint(250, 600)
        tab.scroll.down(dy)
        sleep_range(*pause)
