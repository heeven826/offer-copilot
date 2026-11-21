"""AI 回复决策：根据未读会话的最新消息，决定回复、发简历还是跳过。"""
import json

from loguru import logger

from .client import chat_json

SYSTEM_PROMPT = """你是候选人（资深技术人，背景见下方画像）的求职助理。
给你一段与招聘方的聊天记录（含双方最新消息），请决定下一步动作，只输出 JSON：
{"action": "resume" 或 "reply" 或 "skip", "text": "reply时的回复内容，其他动作留空", "note": "20字内决策依据"}

判定规则：
- 对方明确索要简历/作品集 → resume；
- 对方提问（薪资期望、到岗时间、经历、技术细节、面试邀约等）→ reply：
  以候选人第一人称回答，语气专业简洁（50字内），只依据画像事实作答、不编造；
  谈薪资可给区间并礼貌反问岗位预算；收到面试邀约则表示感谢并确认时间；
- 对方只是礼貌打招呼或简单回应 → reply 一句简短自然推进（如约时间细聊）；
- 对方明确拒绝/表示不合适/已读不回的旧消息 → skip。"""


def decide_reply(friend: dict, messages: list, profile: dict) -> dict:
    """messages: [(is_self, text), ...] 按时间顺序。返回 {"action","text","note"}。"""
    lines = []
    for is_self, text in messages[-8:]:
        who = "我" if is_self else "对方"
        lines.append(f"{who}: {text}")
    info = {
        "对方": f"{friend.get('name','')}（{friend.get('brandName','')} {friend.get('title','')}）",
        "关联职位ID": friend.get("encryptJobId", ""),
    }
    user = (f"【对话记录】\n" + "\n".join(lines) +
            f"\n\n【对方信息】\n{json.dumps(info, ensure_ascii=False)}"
            f"\n\n【求职画像】\n{json.dumps(profile, ensure_ascii=False)}")
    try:
        result = chat_json(SYSTEM_PROMPT, user, temperature=0.3)
        action = result.get("action", "skip")
        if action not in ("resume", "reply", "skip"):
            action = "skip"
        return {"action": action, "text": str(result.get("text", "")),
                "note": str(result.get("note", ""))}
    except Exception as e:
        logger.warning(f"回复决策失败，保守跳过: {e}")
        return {"action": "skip", "text": "", "note": "AI决策失败"}
