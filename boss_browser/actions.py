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
from .risk import RiskControlError, check_risk

# 2026-11-21 实测有效
GREET_READY = True

CHAT_URL = "https://www.zhipin.com/web/geek/chat"

SELECTORS = {
    "job_link": "css:a.job-name",          # 卡片职位名链接（href 含 encryptJobId）
    # 「立即沟通」两套：期望页内嵌详情面板 / 独立详情页
    "greet_buttons": ["css:a.op-btn-chat", "css:a.btn-startchat"],
    # 职位描述两套：内嵌面板 / 独立详情页
    "job_descs": ["css:div.job-detail-body p.desc", "css:.job-sec-text"],
    # 聊天输入框两套（2026-11 实测：contenteditable 为主，textarea 为镜像/变体；
    # 注意：未打开会话时输入区不渲染，先确认会话已打开再查）
    "chat_inputs": ["css:div.chat-input", "css:textarea.input"],
    # 发送按钮两套：button.btn-send（主）/ div.btn-sure-v2 文本=发送（变体）
    # ⚠️ R24-2/R25-1：按钮上方叠着 tooltip 图标层，坐标点击会落在覆盖层导致静默不发送。
    # 统一发送实现见 send_and_verify()：Enter 优先、按钮仅作兜底。
    "chat_sends": ["css:button.btn-send",
                   'xpath://div[contains(@class,"btn-sure-v2")][normalize-space(text())="发送"]'],
    # 会话列表（R23-1：新 UI 点「立即沟通」不再自动打开会话，需按 HR 名定位）
    "conv_item": "css:li[role=listitem]",
    "conv_name": "css:span.name-text",
    # 会话头（R30：身份校验锚点，形如「石女士/猎头顾问/AI4S解决方案产品专家/60-80K」）
    "conv_headers": ["css:.top-info-content", "css:.chat-top-info", "css:.message-top-info"],
    # 详情页职位名（R29-1：防 JD 串页）
    "detail_names": ["css:.job-banner .name", "css:.job-detail-info .name",
                     "css:.job-info-primary .name", "css:h1"],
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

    def _audit(self, action: str, target: str = "", outcome: str = "ok", **detail):
        """操作审计（REQ-10）。db 缺失时静默跳过（测试环境）。"""
        try:
            if self.db:
                self.db.audit(action, target, outcome, **detail)
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

    def _first_ele(self, selectors: list, timeout: int = 3):
        """按顺序尝试多个选择器，返回第一个命中的元素。

        注意：DrissionPage 的 ele() 在元素不存在时可能抛 ElementNotFoundError
        （而非返回 NoneElement，取决于版本/配置）——必须 try/except，
        否则多选择器兜底形同虚设（2026-11 真实踩坑）。
        """
        for s in selectors:
            try:
                e = self.tab.ele(s, timeout=timeout)
            except Exception:
                e = None
            if e:
                return e
        return None

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
        via = "card" if link else "url"

        # R29-1：防 JD 串页——读到的是别家岗位（页面上残留的详情面板）时，直接开详情页重读
        if jd and job is not None and not self._detail_matches(job):
            logger.warning(f"详情页与目标职位不符，回退 URL 直开: {job.brief()}")
            self.tab.get(f"https://www.zhipin.com/job_detail/{eid}.html")
            sleep_range(2.5, 4.0, "等待详情页加载")
            jd = self.read_job_desc()
            via = "url_retry"
            if jd and not self._detail_matches(job):
                self._audit("detail", target=eid, outcome="warn",
                            jd_len=len(jd), via=via, mismatch=True)
                logger.warning("URL 直开仍与目标职位不符，丢弃该 JD")
                return ""

        if jd:
            logger.debug(f"已抓取职位描述 {len(jd)} 字")
        self._audit("detail", target=eid, outcome="ok" if jd else "warn",
                    jd_len=len(jd), via=via)
        return jd

    def _detail_title(self) -> str:
        for sel in SELECTORS["detail_names"]:
            try:
                e = self.tab.ele(sel, timeout=1)
                if e:
                    t = (e.text or "").strip()
                    if t:
                        return self._norm(t)
            except Exception:
                continue
        return ""

    def _detail_matches(self, job) -> bool:
        """详情页标题是否属于目标职位（R29-1）。无法读取标题时按不符处理（宁可重试）。"""
        title = self._norm(getattr(job, "title", "") or "")
        seen = self._detail_title()
        if not seen or not title:
            return False
        return title[:6] in seen or seen[:6] in title

    def greet(self, greeting_text: str, job=None, detail_open: bool = False) -> dict:
        """发送打招呼话术。返回 {"ok": bool, "stage": str, "verified": bool, "note": str}。

        stage 取值（供调用方/agent 决策）：
          open_detail      详情打不开（卡片消失且无ID）
          greet_button     找不到「立即沟通」（职位关闭或已下线）
          already_in_conversation  按钮显示「继续沟通」，已在会话中
          chat_input       会话已建立但找不到输入框（注意：空会话已产生）
          send_button      已输入但找不到发送按钮
          send_disabled    发送按钮始终不可用
          verify_failed    已点击发送但未在会话中确认到消息
          done             成功且已验证
        detail_open=True 表示详情面板已由 open_detail 打开（省一次定位）。
        """
        result = {"ok": False, "stage": "", "verified": False, "note": ""}

        _start_ts = time.time()
        _truncated = False

        def fail(stage, note=""):
            result.update(stage=stage, note=note)
            logger.warning(f"打招呼失败[{stage}]: {note}")
            self._audit("greet", target=getattr(job, "job_id", "") or "",
                        outcome="fail", stage=stage, msg_len=len(greeting_text),
                        truncated=_truncated, duration=round(time.time() - _start_ts, 1))
            try:
                self._back()
            except Exception:
                pass
            return result

        try:
            # 输入框 maxlength=300，超长先截断到句子边界
            if len(greeting_text) > 290:
                _truncated = True
                cut = max(greeting_text.rfind(". ", 0, 290),
                          greeting_text.rfind("。", 0, 290),
                          greeting_text.rfind("！", 0, 290))
                greeting_text = greeting_text[:cut + 1] if cut > 100 else greeting_text[:290]
                logger.warning(f"话术超长，已截断至 {len(greeting_text)} 字符")

            if job is not None and not detail_open:
                if self.open_detail(job) is False:
                    return fail("open_detail", "未找到职位卡片且无ID")

            btn = self._first_ele(SELECTORS["greet_buttons"], timeout=5)
            resumed = False
            if not btn:
                # R23-2：「继续沟通」= 已有会话（可能是之前失败留下的空会话）→ 续发，而不是判失败
                try:
                    cont = self.tab.ele('xpath://a[contains(@class,"op-btn") '
                                        'and contains(text(),"继续沟通")]', timeout=2)
                except Exception:
                    cont = None
                if not cont:
                    return fail("greet_button", "职位可能已关闭/下线")
                logger.info("按钮为「继续沟通」——已有会话，走续发")
                btn = cont
                resumed = True
            self._jitter_click(btn, (-40, 40), (-8, 8))
            sleep_range(2, 3.5, "等待聊天页")
            check_risk(self.tab)
            # 注意：走到这里会话已建立 —— 之后的失败都会留下空会话（消息未发）

            # R23-1：新 UI 不自动打开会话 —— 按 HR 名定位（定位不到就放弃，绝不猜人）
            if not self.ensure_conversation_open(job):
                return fail("conversation_not_open",
                            "会话未自动打开且按 HR 名定位失败（空会话可能已产生，消息未发）")
            # R30 P0：发送前再断言一次身份——宁可失败，不可发错人
            if not self.identity_ok(job):
                return fail("identity_mismatch",
                            "会话身份与目标 HR/职位不符，拒绝发送（防止发错人）")

            # R24/R25：统一发送实现（Enter 优先、按钮兜底、回读校验）
            sr = self.send_and_verify(greeting_text)
            if not sr["verified"]:
                st = sr["stage"]
                # R28-3：避免 send_send_xxx 双前缀
                stage = st if st.startswith("send_") else f"send_{st}"
                return fail(stage, "已输入但未能在会话中确认消息（空会话已产生，消息未发）")
            self._set_ts("last_greet_ts")     # 跨进程消息节奏锚点
            self._audit("greet", target=getattr(job, "job_id", "") or "",
                        outcome="ok", stage="done", verified=True, resumed=resumed,
                        send_via=sr["stage"], msg_len=len(greeting_text),
                        truncated=_truncated, duration=round(time.time() - _start_ts, 1))
            result.update(ok=True, verified=True, stage="done",
                          note="续发（已有会话）" if resumed else "")
            logger.success(f"已发送打招呼(verified=True, {sr['stage']}): {greeting_text[:36]}…")
            self._back()
            return result
        except RiskControlError as e:
            self._audit("risk", target=getattr(job, "job_id", "") or "",
                        outcome="warn", where="greet", keyword=str(e)[:40])
            return fail("risk", "触发平台安全验证——已暂停，请人工在浏览器完成后重试")
        except Exception as e:
            return fail("exception", f"{type(e).__name__}: {e}"[:120])

    def _press_enter(self) -> bool:
        """键盘 Enter 发送（R25-3：Enter 远优于按钮坐标点击）。

        R28-2：窗口最小化等状态下 `actions.type` 会静默失效 → **CDP 派发优先**，
        actions.type 仅作兜底。
        """
        try:
            self.tab.run_cdp("Input.dispatchKeyEvent", type="keyDown", key="Enter",
                             code="Enter", windowsVirtualKeyCode=13,
                             nativeVirtualKeyCode=13, text="\r")
            self.tab.run_cdp("Input.dispatchKeyEvent", type="keyUp", key="Enter",
                             code="Enter", windowsVirtualKeyCode=13,
                             nativeVirtualKeyCode=13)
            return True
        except Exception as e:
            logger.debug(f"CDP Enter 失败，回落 actions.type: {e}")
        try:
            from DrissionPage.common import Keys
            self.tab.actions.type(Keys.ENTER)
            return True
        except Exception as e:
            logger.debug(f"Enter 发送失败: {e}")
            return False

    def _input_has_text(self) -> bool:
        """输入框是否还有未发出的内容（contenteditable 与 textarea 两种都查）。"""
        try:
            inp = self._first_ele(SELECTORS["chat_inputs"], timeout=2)
            if not inp:
                return False
            txt = (inp.text or "").strip()
            if not txt:
                txt = (inp.attr("value") or "").strip()
            return bool(txt)
        except Exception:
            return False

    def send_and_verify(self, text: str, timeout_input: int = 15) -> dict:
        """统一发送实现（R24-4/R25-1）：Enter 优先 → 按钮兜底 → 回读校验。

        **只有回读到我方消息才算成功**——返回 {"ok": bool, "stage": str, "verified": bool}。
        调用方必须已打开目标会话。greet/reply 共用本实现，不再各自点按钮。
        """
        inp = self._first_ele(SELECTORS["chat_inputs"], timeout=timeout_input)
        if not inp:
            return {"ok": False, "stage": "chat_input", "verified": False}
        # R28-1：窗口最小化时编辑器塌成 0 高度，输入/Enter 都会静默失效
        try:
            h = inp.rect.size.height
            if h is not None and h <= 10:
                logger.warning(f"聊天编辑器高度 {h}px——窗口可能被最小化")
                return {"ok": False, "stage": "editor_collapsed", "verified": False}
        except Exception:
            pass
        try:
            self._jitter_click(inp, (-40, 40), (-3, 3))
        except Exception:
            pass
        sleep_range(0.5, 1.2)
        try:
            inp.input(text)
        except Exception:
            try:
                inp.input(text, by_js=True)
            except Exception:
                return {"ok": False, "stage": "input_failed", "verified": False}
        sleep_range(0.8, 1.6)

        # ① Enter 发送（主路径）
        self._press_enter()
        sleep_range(1.2, 2.0)
        if self._verify_sent(text, timeout=6):
            return {"ok": True, "stage": "done", "verified": True}

        # ② 按钮兜底：仅当输入框仍有内容（Enter 未生效）时才点，且等 disabled 解除
        if self._input_has_text():
            send = self._first_ele(SELECTORS["chat_sends"], timeout=3)
            if send:
                for _ in range(6):
                    if "disabled" not in (send.attr("class") or ""):
                        break
                    sleep_range(0.5, 1.0)
                if "disabled" not in (send.attr("class") or ""):
                    self._jitter_click(send, (-15, 15), (-4, 4))
                    sleep_range(1.5, 2.5)
                    if self._verify_sent(text, timeout=6):
                        return {"ok": True, "stage": "done_btn", "verified": True}
        return {"ok": False, "stage": "send_unverified", "verified": False}

    def _conv_header_text(self) -> str:
        """读会话头文本（R30 身份锚点）。"""
        for sel in SELECTORS["conv_headers"]:
            try:
                e = self.tab.ele(sel, timeout=1)
                if e:
                    t = (e.text or "").strip()
                    if t:
                        return t
            except Exception:
                continue
        return ""

    @staticmethod
    def _norm(s: str) -> str:
        import re as _re
        return _re.sub(r"\s+", "", s or "")

    def identity_ok(self, job) -> bool:
        """**发送前身份校验（R30 P0）**：会话头必须同时含目标 HR 名与职位/公司信号。

        输入框存在 ≠ 会话正确（可能是上一个会话）。校验不通过一律不发：
        宁可失败，不可发错人。
        """
        return self.identity_ok_name(
            getattr(job, "hr_name", "") or "",
            getattr(job, "title", "") or "",
            getattr(job, "company", "") or "")

    def identity_ok_name(self, hr: str, title: str = "", company: str = "") -> bool:
        """按「HR + 职位/公司」校验当前已打开会话的身份（reply/resume 也用它）。"""
        hr = self._norm(hr)
        header = self._norm(self._conv_header_text())
        if not hr or not header:
            logger.warning(f"身份校验失败：HR='{hr}' header_len={len(header)}")
            return False
        if hr not in header:
            logger.warning(f"身份校验失败：会话头不含 HR「{hr}」→ {header[:40]}")
            return False
        title = self._norm(title or "")
        company = self._norm(company or "")
        # 标题在会话头里可能被截断：用前缀 6/4 字匹配；公司名作为并列信号
        checks = []
        if title:
            checks += [title[:6], title[:4]]
        if company:
            checks.append(company)
        checks = [c for c in checks if c]
        if not checks:
            logger.warning("身份校验失败：无职位名/公司名可校验")
            return False
        if any(c in header for c in checks):
            return True
        logger.warning(f"身份校验失败：会话头与职位/公司不符（{title[:12]}/{company[:12]}）")
        return False

    def _find_conv_by_hr(self, hr: str):
        """在会话列表按 HR 姓名精确定位可点击元素（找不到返回 None，绝不猜人）。"""
        try:
            items = self.tab.eles(SELECTORS["conv_item"])
        except Exception:
            return None
        for it in items:
            try:
                nm = it.ele(SELECTORS["conv_name"], timeout=0.5)
                if nm and (nm.text or "").strip() == hr:
                    inner = it.ele("css:div.friend-content", timeout=0.5)
                    return inner or it
            except Exception:
                continue
        return None

    def ensure_conversation_open(self, job=None, timeout: int = 15) -> bool:
        """确保聊天页打开的是 **job 对应的会话**（R23-1 + R30 P0）。

        R30 教训：**输入框存在 ≠ 会话正确**——输入框可能属于上一个会话。
        因此判据只有一个：`identity_ok(job)`（会话头含目标 HR + 职位/公司）。
        """
        deadline = time.time() + timeout
        hr = (getattr(job, "hr_name", "") or "").strip()
        if not hr:
            logger.warning("缺少 HR 姓名，无法安全定位会话（不猜人）")
            return False
        while time.time() < deadline:
            if self.identity_ok(job):
                return True
            if "/web/geek/chat" not in (self.tab.url or ""):
                self.tab.get(CHAT_URL)
                sleep_range(2, 3, "等待聊天页加载")
            item = self._find_conv_by_hr(hr)
            if item:
                logger.info(f"按 HR 名定位会话: {hr}")
                try:
                    item.click(by_js=True)          # R30：DOM 点击优先，避免坐标命中邻居
                except Exception:
                    self._jitter_click(item, (-60, 60), (-5, 5))
                sleep_range(2, 3.5, "等待会话打开")
            else:
                time.sleep(1.5)   # 新会话可能还在渲染，稍等再试
        return self.identity_ok(job)

    def _verify_sent(self, text: str, timeout: int = 6) -> bool:
        """读当前会话最后几条我方消息，确认与发送内容匹配。"""
        deadline = time.time() + timeout
        key = text[:30].strip()
        while time.time() < deadline:
            try:
                items = self.tab.eles("css:li.message-item")
                for it in reversed(items[-5:]):
                    if "item-myself" in (it.attr("class") or ""):
                        t = ""
                        try:
                            e = it.ele("css:.text-content", timeout=0.5)
                            t = (e.text if e else it.text or "").strip()
                        except Exception:
                            t = (it.text or "").strip()
                        if key and key[:15] in t:
                            return True
                        break
            except Exception:
                pass
            time.sleep(1)
        return False

    def send_resume_card(self) -> bool:
        """聊天窗口发送简历卡片 —— 交互未实测，保持 TODO。"""
        logger.warning("发送简历卡片尚未实现（TODO）")
        return False

    def _back(self):
        """从聊天页返回职位列表。"""
        self.tab.back()
        sleep_range(1.5, 2.5, "返回职位列表")
