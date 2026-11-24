"""收件箱处理：查未读会话 → 读消息 → AI 决策（回复 / 发简历）。

结构（2026-11-21 实测）：
- 会话列表接口: /wapi/zprelation/friend/getGeekFriendList.json
  （字段 unreadMsgCount/name/brandName/title/lastMsg/encryptJobId 等，83条全量）
- 会话列表 DOM: li[role=listitem] > span.name-text
- 消息区: div.chat-record > li.message-item（含 item-myself 为我方，span.text-content 为正文）
- 回复: div.chat-input + button.btn-send（与打招呼同一套）
- 发简历: 工具栏「发简历」按钮（双方回复后可用，class 含 unable 为禁用）
  → 弹层 div.upload-select-dialog 中「发送在线简历」卡片
"""
import random
import time

from loguru import logger

from ai.reply import decide_reply
from .actions import CHAT_URL, SELECTORS as ACT_SELECTORS
from .human import sleep_range
from .risk import check_risk

FRIEND_API = "/wapi/zprelation/friend/getGeekFriendList.json"

# 选择器单一来源（B2 教训）：会话/输入框/发送按钮统一引用 actions.SELECTORS
SELECTORS = {
    "conv_item": ACT_SELECTORS["conv_item"],
    "conv_name": ACT_SELECTORS["conv_name"],
    "chat_inputs": ACT_SELECTORS["chat_inputs"],
    "chat_sends": ACT_SELECTORS["chat_sends"],
    "msg_list": "css:div.chat-record",
    "msg_item": "css:li.message-item",
    "msg_text": "css:.text-content",
    "resume_btn": 'xpath://div[contains(@class,"toolbar-btn") and normalize-space(text())="发简历"]',
    "resume_online": 'xpath://p[contains(text(),"发送在线简历")]',
}


class InboxProcessor:
    def __init__(self, tab, cfg: dict, db, profile: dict):
        self.tab = tab
        self.cfg = cfg
        self.db = db
        self.profile = profile

    def _audit(self, action: str, target: str = "", outcome: str = "ok", **detail):
        """操作审计（REQ-10，R14-2 补齐）。"""
        try:
            if self.db:
                self.db.audit(action, target, outcome, **detail)
        except Exception:
            pass

    @staticmethod
    def _jitter_click(ele, xr, yr):
        try:
            ele.click.by_offset(random.randint(*xr), random.randint(*yr))
        except Exception:
            ele.click()

    # ---------- 数据获取 ----------

    def _capture_friends(self) -> list:
        """重载聊天页并监听会话列表接口。"""
        self.tab.listen.start(FRIEND_API)
        try:
            self.tab.get(CHAT_URL)
            sleep_range(3, 5, "等待聊天页加载")
            for packet in self.tab.listen.steps(timeout=12):
                try:
                    zp = (packet.response.body or {}).get("zpData") or {}
                    fl = zp.get("result") or []
                    if isinstance(fl, list) and fl:
                        logger.info(f"捕获会话列表: {len(fl)} 条")
                        return fl
                except Exception:
                    continue
        finally:
            self.tab.listen.stop()
        return []

    def unread_conversations(self, max_n: int) -> list:
        friends = self._capture_friends()
        unread = [f for f in friends if (f.get("unreadMsgCount") or 0) > 0]
        logger.info(f"未读会话: {len(unread)} 条")
        return unread[:max_n]

    # ---------- 交互 ----------

    def _find_conversation(self, friend: dict):
        """在会话列表 DOM 中按姓名+公司定位会话项。

        2026-11 实测：li 本身点击已失效，必须点内部 .friend-content。
        """
        name = friend.get("name") or ""
        brand = friend.get("brandName") or ""
        for it in self.tab.eles(SELECTORS["conv_item"]):
            try:
                nm = it.ele(SELECTORS["conv_name"], timeout=0.5)
                if nm and (nm.text or "").strip() == name:
                    if not brand or brand in (it.text or ""):
                        # 返回内部可点击元素
                        inner = it.ele("css:div.friend-content", timeout=0.5)
                        return inner or it
            except Exception:
                continue
        return None

    def _read_messages(self, limit: int = 8) -> list:
        """读取当前会话最近的消息 [(is_self, text), ...]。"""
        rec = self.tab.ele(SELECTORS["msg_list"], timeout=4)
        if not rec:
            return []
        msgs = []
        for it in rec.eles(SELECTORS["msg_item"])[-limit:]:
            try:
                is_self = "item-myself" in (it.attr("class") or "")
                t = it.ele(SELECTORS["msg_text"], timeout=0.5)
                text = (t.text or "").strip() if t else (it.text or "").strip()
                if text:
                    msgs.append((is_self, text[:200]))
            except Exception:
                continue
        return msgs

    def _reply(self, text: str) -> dict:
        """在当前会话发送回复 —— 复用统一发送实现（R24-1：Enter 优先 + 回读校验）。

        返回 {"ok","stage","verified"}；**verified=False 即未发出**，
        cmd_reply 不得据此记成功（R24-3：虚假成功历史污染统计）。
        """
        from .actions import Communicator
        comm = Communicator(self.tab, self.cfg, self.db)
        return comm.send_and_verify(text)

    def _send_resume(self) -> bool:
        """点击「发简历」→ 选择「发送在线简历」。"""
        btn = self.tab.ele(SELECTORS["resume_btn"], timeout=3)
        if not btn:
            logger.warning("未找到「发简历」按钮")
            return False
        if "unable" in (btn.attr("class") or ""):
            logger.info("「发简历」按钮当前不可用（需双方回复过），跳过")
            return False
        self._jitter_click(btn, (-25, 25), (-5, 5))
        sleep_range(1, 2, "等待简历弹层")
        opt = self.tab.ele(SELECTORS["resume_online"], timeout=4)
        if not opt:
            logger.warning("未找到「发送在线简历」选项")
            return False
        # 点它所在的选项卡片
        try:
            card = opt.parent().parent()
            self._jitter_click(card, (-60, 60), (-15, 15))
        except Exception:
            self._jitter_click(opt, (-20, 20), (-5, 5))
        sleep_range(1.5, 2.5)
        logger.success("已发送在线简历")
        return True

    # ---------- 主流程 ----------

    def collect_unread(self, max_conv: int = 5) -> list:
        """只读采集：未读会话 + 消息内容（不做任何回复动作，决策交给上层）。

        返回 [{"name","brand","title","unread","job_id","messages":[{"who","text"}]}]
        """
        result = []
        unread = self.unread_conversations(max_conv)
        self._audit("inbox", outcome="ok", unread=len(unread), max=max_conv)
        for f in unread:
            name = f.get("name") or "?"
            entry = {
                "name": name,
                "brand": f.get("brandName") or "",
                "title": f.get("title") or "",
                "unread": f.get("unreadMsgCount") or 0,
                "job_id": f.get("encryptJobId") or "",
                "messages": [],
            }
            try:
                item = self._find_conversation(f)
                if not item:
                    entry["error"] = "会话列表中未找到（可能被折叠）"
                    result.append(entry)
                    continue
                self._jitter_click(item, (-80, 80), (-6, 6))
                sleep_range(2, 3.5, "等待会话打开")
                for is_self, text in self._read_messages():
                    entry["messages"].append(
                        {"who": "me" if is_self else "hr", "text": text})
            except Exception as e:
                entry["error"] = str(e)[:80]
            result.append(entry)
            sleep_range(3, 6, "会话间间隔")
        return result

    def find_and_open(self, name: str, brand: str = "") -> bool:
        """按姓名+公司打开指定会话（供 CLI reply/resume 用）。

        B3 修复：connect() 会把页面带到首页，会话列表不在 DOM——
        这里无条件确保先在聊天页。
        """
        if "/web/geek/chat" not in (self.tab.url or ""):
            self.tab.get(CHAT_URL)
            sleep_range(2.5, 3.5, "等待聊天页加载")
        friend = {"name": name, "brandName": brand}
        item = self._find_conversation(friend)
        if not item:
            self._audit("open_conversation", target=name, outcome="fail", brand=brand)
            return False
        self._jitter_click(item, (-80, 80), (-6, 6))
        sleep_range(2, 3, "等待会话打开")
        return True

    def run(self, max_conv: int = 5, send_enabled: bool = True) -> dict:
        """处理未读会话：读取 → AI 决策 → 回复或发简历。"""
        stats = {"replied": 0, "resume": 0, "skipped": 0, "failed": 0}
        unread = self.unread_conversations(max_conv)
        for f in unread:
            name = f.get("name") or "?"
            brand = f.get("brandName") or ""
            logger.info(f"--- 处理未读会话: {name}（{brand} {f.get('title','')}）")
            try:
                item = self._find_conversation(f)
                if not item:
                    logger.warning("会话列表中未找到该会话（可能被折叠），跳过")
                    stats["skipped"] += 1
                    continue
                self._jitter_click(item, (-80, 80), (-6, 6))
                sleep_range(2, 3.5, "等待会话打开")
                check_risk(self.tab)

                msgs = self._read_messages()
                counterpart = [t for self_flag, t in msgs if not self_flag]
                if not counterpart:
                    logger.info("没有对方消息，跳过")
                    stats["skipped"] += 1
                    continue

                decision = decide_reply(f, msgs, self.profile)
                action = decision.get("action", "skip")
                text = decision.get("text", "")
                logger.info(f"AI 决策[{action}]: {text[:60]}")

                if action == "resume":
                    if send_enabled:
                        if self._send_resume():
                            stats["resume"] += 1
                            self.db.record_reply(name, brand, "resume", "")
                        else:
                            stats["failed"] += 1
                    else:
                        logger.info("[观察模式] 仅记录，不实际发送简历")
                        self.db.record_reply(name, brand, "resume-planned", text)
                elif action == "reply" and text:
                    if send_enabled:
                        sr = self._reply(text)
                        if sr.get("verified"):
                            stats["replied"] += 1
                            self.db.record_reply(name, brand, "reply", text)
                        else:
                            stats["failed"] += 1
                            logger.warning(f"回复未送达[{sr.get('stage')}]，未记录成功")
                    else:
                        logger.info(f"[观察模式] 仅记录回复: {text}")
                        self.db.record_reply(name, brand, "reply-planned", text)
                else:
                    stats["skipped"] += 1
                    self.db.record_reply(name, brand, "skip", decision.get("note", ""))

                sleep_range(4, 9, "会话间随机间隔")
            except Exception as e:
                logger.error(f"处理会话失败({name}): {e}")
                stats["failed"] += 1
        return stats
