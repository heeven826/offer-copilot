"""风控检测：识别安全验证页面，暂停等待人工处理。

原则：本工具不自动破解任何验证码 —— 检测到验证时暂停程序，
由用户在浏览器中手动完成后继续。这是对账号最安全的方式。
"""
from loguru import logger

# 页面命中任意关键词即认为触发风控
RISK_KEYWORDS = ["安全验证", "请点击按钮完成验证", "访问异常", "滑动验证", "异常请求"]


class RiskControlError(Exception):
    """触发平台风控（安全验证等），需要人工介入。"""


def check_risk(tab) -> None:
    """检查当前页面是否处于风控/验证状态，是则抛出 RiskControlError。"""
    try:
        html = tab.html or ""
    except Exception:
        return
    for kw in RISK_KEYWORDS:
        if kw in html:
            logger.warning(f"检测到风控关键词「{kw}」")
            raise RiskControlError(kw)
