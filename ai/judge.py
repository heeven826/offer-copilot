"""AI 职位评估：职位信息 + 求职画像 → 匹配分数 / 理由 / 红线。

两阶段使用同一评估器，阈值不同：
- 阶段1（粗筛，仅列表信息）: min_score = ai.stage1_score (默认6)
- 阶段2（精评，含完整 JD）:   min_score = ai.min_score     (默认7)

should_apply 由程序按规则推导（score ≥ 阈值 且 无红线），
不让 LLM 输出 —— 避免分数与建议自相矛盾。
"""
import json
from dataclasses import dataclass

from loguru import logger

from storage.models import Job
from .client import chat_json

SYSTEM_PROMPT = """你是一个严谨的求职助理。用户会给你一个职位的信息和他的求职画像，
请综合评估匹配程度，只输出一个 JSON，不要输出其他任何内容，格式：
{"score": 0到10的整数, "reasons": "50字内的核心理由", "redline": true或false}

评分维度与权重（总分10）：
- 方向匹配 40%：岗位核心方向与画像目标方向（见画像 target_roles）的契合度；
  方向性错配（岗位核心业务与目标方向无关）最高只能给 3 分；
- 技能匹配 25%：技能栈与候选人经验的可迁移性；
- 职级定位 20%：职级与候选人资历（见画像年限）的匹配；纯初级执行岗应扣分；
- 薪资匹配 15%：岗位薪资下限低于用户期望下限才扣分，高于期望不扣分（向上兼容）；
  年限要求低于实际年限不扣分（资深不是劣势）。

redline=true 仅当存在一票否决项：岗位与画像方向明确冲突、命中用户明确不接受的方向、
工作地点与期望城市不符且非远程、或其他不可接受条件；拿不准给 false。

职位描述为空（列表粗筛）时：基于现有信息谨慎评分，方向存疑的给 5 分左右并在
reasons 里写明"需详情确认"。"""
# 注：地点一票否决已由本地硬过滤（filters.py）承担，AI 的 redline 是第二道保险。


@dataclass
class JudgeResult:
    score: int
    reasons: str
    redline: bool
    should_apply: bool      # 程序推导：score ≥ min_score 且 无红线


def evaluate_job(job: Job, profile: dict, min_score: int, stage: str = "") -> JudgeResult:
    """评估单个职位。stage 用于日志区分（粗筛/精评）。"""
    info = {
        "职位名": job.title,
        "薪资": job.salary,
        "公司": job.company,
        "公司信息": job.company_info,
        "地点": job.location,
        "经验要求": job.experience,
        "学历": job.education,
        "技能标签": job.tags,
        "职位描述": job.job_desc[:1500] if job.job_desc else "(未获取详情，仅列表信息)",
    }
    user = (f"【职位信息】\n{json.dumps(info, ensure_ascii=False)}\n\n"
            f"【求职画像】\n{json.dumps(profile, ensure_ascii=False)}")

    result = chat_json(SYSTEM_PROMPT, user)
    score = int(result.get("score", 0))
    redline = bool(result.get("redline", False))
    jr = JudgeResult(
        score=score,
        reasons=str(result.get("reasons", "")),
        redline=redline,
        should_apply=(score >= min_score and not redline),
    )
    tag = f"[{stage}]" if stage else ""
    logger.info(f"AI{tag}评分 {jr.score}/10{'(红线!)' if jr.redline else ''} | "
                f"{job.brief()} | {jr.reasons}")
    return jr
