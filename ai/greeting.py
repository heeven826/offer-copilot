"""AI 生成个性化打招呼话术 —— 按招聘方身份定制侧重点，按 JD 语言切换中英文。

- business（人事/HR/老板/创始人/总经理）：展示业务与交付价值，讲成果数字，少堆技术名词
- tech（CTO/技术总监/架构师/研发经理）：展示技术硬实力与架构判断力
- balanced（无法判断）：业务成果与技术深度各点一个
- JD 为英文时 → 用英文写话术（同样的身份适配规则）
"""
import json
import re

from loguru import logger

from storage.models import Job
from .client import chat_text

BUSINESS_KEYWORDS = ("老板", "创始人", "ceo", "CE0", "总裁", "总经理", "合伙人",
                     "人事", "hr", "HR", "Hr", "招聘", "行政", "coo", "COO", "董事长")
TECH_KEYWORDS = ("技术", "cto", "CTO", "架构", "研发", "算法", "工程", "开发",
                 "总监", "经理", "leader", "Lead", "AI", "ai")

_ENGLISH_HINTS = {"experience", "team", "development", "responsible", "engineering",
                  "management", "requirements", "business", "platform", "system",
                  "data", "software", "product", "years", "strong", "skills",
                  "llm", "agent", "model", "design", "build", "work"}


def is_english_text(text: str) -> bool:
    """判断一段文本是否为英文（ASCII 字母占比 + 常见英文词佐证）。"""
    if not text or len(text) < 40:
        return False
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return False
    ascii_ratio = sum(1 for c in letters if ord(c) < 128) / len(letters)
    if ascii_ratio < 0.7:
        return False
    words = set(w.lower() for w in re.findall(r"[A-Za-z]{2,}", text))
    return len(words & _ENGLISH_HINTS) >= 3


def detect_audience(job: Job) -> str:
    """根据 HR 头衔判断对方视角。"""
    t = f"{job.hr_title or ''} {job.hr_name or ''}"
    if any(k in t for k in BUSINESS_KEYWORDS):
        return "business"
    if any(k in t for k in TECH_KEYWORDS):
        return "tech"
    return "balanced"


def _persona(profile: dict) -> tuple:
    """从画像提取姓名与年限（提示词动态引用，不硬编码个人信息）。"""
    basics = (profile or {}).get("basics") or {}
    name = basics.get("name") or "候选人"
    years = basics.get("years_of_experience") or "资深"
    return str(name), str(years)


def _system_prompt_en(audience: str, name: str, years: str) -> str:
    """英文 JD 场景的提示词。"""
    base = f"""You are the job-search assistant of a senior candidate ({name}, {years} years of experience — full profile provided below). Write the candidate's FIRST outreach message to the recruiter. Write in ENGLISH.
Rules:
- HARD LIMIT: the message must be under 280 characters (including spaces) — the chat box truncates at 300. Aim for 2 sentences, 30-40 words;
- open with who the candidate is (e.g., "I'm {name}, ... with {years} years in ...");
- confident, peer-level professional tone; no pleading or student-like phrasing;
- end with a natural call to action (e.g., "would love to set up a quick chat");
- achievements MUST come from the profile's highlights; keep the numbers exact; translate into natural native English;
- output ONLY the message text — no quotes, no explanation."""
    if audience == "business":
        base += ("\nAudience: HR / founder / business owner — emphasize business outcomes and delivery "
                 "results with numbers from the profile highlights; minimize technical jargon.")
    elif audience == "tech":
        base += ("\nAudience: technical leader (CTO / architect / eng director) — highlight the 1-2 "
                 "hardest technical achievements from the profile highlights; "
                 "show technical judgment, not tool lists.")
    else:
        base += "\nAudience unknown — balance one business outcome with one technical depth point."
    return base


def _system_prompt(audience: str, name: str, years: str) -> str:
    base = f"""你是资深候选人的求职助理，帮 {name}（{years}年经验，完整画像见下方）给招聘方写第一条打招呼消息。
硬性要求：
- 70~90字，2~3句话；第一句自报家门（姓名+年限+定位，按画像）；
- 语气沉稳自信、平级专业人士的开场感；禁用"求""麻烦您""打扰了"等示弱词和学生腔；
- 结尾用一句自然的推进（如"方便的话想和您约个时间细聊"），不用"期待您的回复"这类被动句式；
- 成果数字必须来自画像的 highlights，精确引用，不得编造；
- 只输出话术本身，不要任何解释、引号或前缀。"""
    if audience == "business":
        base += """
本次对象是人事/老板/业务方，用业务视角写：
- 展示交付与经营价值：从画像 highlights 里选成果和数字（规模、效果、带队人数等）；
- 少堆技术名词，突出"能把项目独立落地成业务结果"的信号。"""
    elif audience == "tech":
        base += """
本次对象是技术负责人（CTO/技术总监/架构师），用技术视角写：
- 点出画像 highlights 中最有含金量的1~2个技术点；
- 体现技术判断力（选型与架构权衡），而不是罗列工具名。"""
    else:
        base += """
本次对象身份不明：业务成果与技术深度各点一个，平衡呈现。"""
    return base


def generate_greeting(job: Job, profile: dict, fallback: str) -> str:
    """生成打招呼话术；JD 为英文时输出英文。AI 失败时回退到配置的默认话术。"""
    try:
        info = {
            "职位名": job.title,
            "薪资": job.salary,
            "公司": job.company,
            "招聘者": f"{job.hr_name}（{job.hr_title}）",
            "技能标签": job.tags,
            "职位描述摘要": job.job_desc[:600] if job.job_desc else "",
        }
        user = (f"【目标职位】\n{json.dumps(info, ensure_ascii=False)}\n\n"
                f"【求职画像】\n{json.dumps(profile, ensure_ascii=False)}")
        audience = detect_audience(job)
        english = is_english_text(job.job_desc or "")
        name, years = _persona(profile)
        prompt = (_system_prompt_en(audience, name, years) if english
                  else _system_prompt(audience, name, years))
        text = chat_text(prompt, user, temperature=0.7)
        text = text.strip().strip('"').strip()
        if text:
            lang = "EN" if english else "CN"
            logger.info(f"AI 话术[{audience}/{lang}]: {text}")
            return text
    except Exception as e:
        logger.warning(f"话术生成失败，使用默认话术: {e}")
    return fallback
