"""Chrome 接管与登录态管理（基于 DrissionPage）。

工作方式：
- 若 debug_port 上已有 Chrome 调试实例在运行 → 直接接管（复用登录态）；
- 否则用项目内独立的 user_data_dir 启动本机 Chrome（首次需扫码登录一次，
  之后登录态持久保存在 chrome_profile/ 中）。
"""
import time
from pathlib import Path

from DrissionPage import Chromium, ChromiumOptions
from loguru import logger

from .risk import check_risk

ZHIPIN_HOME = "https://www.zhipin.com/"


class BossBrowser:
    def __init__(self, cfg: dict, project_root: Path):
        b = cfg.get("browser", {})
        self.port = b.get("debug_port", 9222)
        self.user_data_dir = (project_root / b.get("user_data_dir", "./chrome_profile")).resolve()
        self.browser = None
        self.tab = None

    def connect(self):
        """连接/启动 Chrome，返回可用的 tab 对象。"""
        opts = ChromiumOptions()
        opts.set_local_port(self.port)
        opts.set_user_data_path(str(self.user_data_dir))
        # 降低自动化特征
        opts.set_argument("--disable-blink-features=AutomationControlled")
        # 注意：不使用 headless —— 无头模式极易被识别

        self.browser = Chromium(opts)   # 已有实例则自动接管
        self.tab = self.browser.latest_tab
        logger.info(f"Chrome 已连接，当前页面: {self.tab.url}")
        return self.tab

    def open(self, url: str):
        """打开 URL 并做风控检查。"""
        self.tab.get(url)
        check_risk(self.tab)
        return self.tab

    # ---------- 登录 ----------

    def ensure_login(self, timeout: int = 300) -> bool:
        """确认已登录；未登录则提示扫码，程序自动轮询检测（不反复刷新页面）。"""
        self.open(ZHIPIN_HOME)
        start = time.time()
        prompted = False
        while time.time() - start < timeout:
            if self._looks_logged_in():
                logger.success("登录态正常")
                return True
            if not prompted:
                logger.warning("未检测到登录态 —— 请在弹出的浏览器窗口中扫码登录，"
                               "登录成功后程序会自动继续（无需其他操作）")
                prompted = True
            time.sleep(5)
        logger.error("等待登录超时")
        return False

    def session_alive(self, max_wait: int = 10) -> bool:
        """真实登录态复核：cookie 可能存在但服务端会话已失效（假阳性）。

        打开期望页看「期望职位标签」是否渲染——只有真登录才会出现。
        """
        from .expect_page import EXPECT_URL, SELECTORS as EP_SEL
        try:
            self.tab.get(EXPECT_URL)
            deadline = time.time() + max_wait
            while time.time() < deadline:
                try:
                    if self.tab.eles(EP_SEL["expect_tabs"], timeout=2):
                        return True
                except Exception:
                    pass
                time.sleep(1.5)
        except Exception:
            pass
        return False

    def _looks_logged_in(self) -> bool:
        """粗略判断登录态。

        TODO(字段提取阶段一起校准)：改用更可靠的元素判断（如头像/用户名节点）。
        当前逻辑：Boss直聘登录后会种下 wt2 / bst 等 cookie。
        注：DrissionPage 的 cookies() 返回 dict 列表。
        """
        try:
            names = {c["name"] if isinstance(c, dict) else c.name()
                     for c in self.tab.cookies()}
        except Exception:
            return False
        return bool(names & {"wt2", "bst", "wbg", "bstbai"})
