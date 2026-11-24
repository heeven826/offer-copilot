"""本地硬过滤：城市、薪资区间带、黑名单 —— 确定性规则前置，不花 AI 调用。

设计原则：能用规则确定杀掉的，绝不交给 LLM 自由裁量（比如城市不符）。
薪资按「折算成 12 薪的有效月薪」比较：
    "40-70K·16薪" → 40×16/12 = 53.3K/月（等价 12 薪的 53.3K）
    "35-60K"      → 35×12/12 = 35K/月
"""
import re

# ============ 期望标签 vs 目标方向 的一致性判断 ============
# 方向关键词（小写子串匹配）：只有这些才算"方向证据"
DIRECTION_KEYWORDS = [
    "ai", "人工智能", "大模型", "llm", "agent", "智能体", "rag", "算法",
    "机器学习", "深度学习", "nlp", "计算机视觉", "语音", "推荐", "搜索",
    "广告", "数据", "平台", "云", "infra", "后端", "前端", "全栈",
    "移动端", "测试", "运维", "安全", "嵌入式", "硬件", "爬虫",
    "fde", "交付", "解决方案", "售前", "产品", "游戏", "电商",
]
# 通用职称/级别词：禁止作为方向证据（"程师""总监"这类片段命中=随机匹配）
STOPWORDS = {"技术", "工程师", "程师", "总监", "负责人", "专家", "经理",
             "合伙人", "开发", "高级", "资深", "实习", "chief", "staff"}


# 连写复合词归一化表（R8-1）：AIoT/AIGC/AIOps 这类会被硬边界规则拦掉，
# 匹配前按整词识别并注入其方向关键词。白名单可枚举、可控。
COMPOUND_WORDS = {
    "aiot": "ai", "aiops": "ai", "aigc": "ai", "aiagent": "ai agent",
    "ainative": "ai", "genai": "ai", "aidata": "ai", "aicoding": "ai",
    "llmops": "llm", "ragflow": "rag", "ragchain": "rag",
}


def direction_signal(text: str) -> list:
    """文本中命中的方向关键词（大小写与边界规则同 compute_alignment）。

    用途：jobs 输出分层——空列表 = "无方向信号"（纯硬件/工艺/通用后端等），
    与"有方向词但与画像不符"区分开，供 agent 区别对待（R7 OBS-3）。
    连写复合词（AIoT/AIGC等）先经归一化表注入关键词（R8-1）。
    """
    if not text:
        return []
    hits = []
    tl = text.lower()
    for compound, kws in COMPOUND_WORDS.items():
        if re.search(rf"(?<![a-zA-Z0-9]){compound}(?![a-zA-Z0-9])", tl):
            hits.extend(kws.split())
    for kw in DIRECTION_KEYWORDS:
        if kw.isascii():
            if re.search(rf"(?<![a-zA-Z0-9]){re.escape(kw)}(?![a-zA-Z0-9])",
                         text, re.IGNORECASE):
                hits.append(kw)
        elif kw in text:
            hits.append(kw)
    return list(dict.fromkeys(hits))   # 去重保序


def direction_match(keywords: list, target_roles: list) -> list:
    """信号关键词与画像目标方向的交集（R8-2 三态判断用）。

    agent 判读：match 非空=正向；keywords 非空但 match 空=负向或无关；
    keywords 空=无方向证据（no_direction_signal）。
    """
    if not keywords or not target_roles:
        return []
    role_text = " ".join(str(r) for r in target_roles)
    rl = role_text.lower()
    matched = []
    for kw in keywords:
        if kw.isascii():
            if re.search(rf"(?<![a-zA-Z0-9]){re.escape(kw)}(?![a-zA-Z0-9])", rl):
                matched.append(kw)
        elif kw in role_text:
            matched.append(kw)
    return matched


def compute_alignment(expect_tabs: list, target_roles: list) -> dict:
    """平台期望标签 vs 画像目标方向的粗略一致性结论。

    只认方向关键词交集（大小写不敏感），通用职称不计入——
    防止 "AI 交付工程师"[-2:]="程师" 命中 "全栈工程师" 这类假阳性。
    - aligned=True/False 有关键词交集与否
    - aligned=None       任一侧缺失，无法判断（不要默认 True）
    仅供快速拦截明显错配；最终由 agent 语义复核（evidence 给出命中三元组）。
    """
    tabs = [t for t in (expect_tabs or []) if t]
    roles = [r for r in (target_roles or []) if r]
    if not tabs or not roles:
        return {"aligned": None, "overlap": [], "evidence": [], "suggestion": "",
                "note": "unknown: 缺少 target_roles 或 expect_tabs，无法判断，请先补全"}

    overlap, evidence = [], []
    for kw in DIRECTION_KEYWORDS:
        if kw in STOPWORDS:   # 契约：DIRECTION_KEYWORDS 不得含停用词（测试有断言）
            continue
        if kw.isascii():
            # ASCII 词只防止英文/数字邻接（trainee/email/storage 里的 ai/rag），
            # 中文邻接是合法的（如 "AI应用"、"RAG工程师"）——中文是 \w，不能用 \b
            pat = re.compile(rf"(?<![a-zA-Z0-9]){re.escape(kw)}(?![a-zA-Z0-9])",
                             re.IGNORECASE)
            hit_roles = [r for r in roles if pat.search(r)]
            hit_tabs = [t for t in tabs if pat.search(t)]
        else:
            # 中文词用子串匹配
            hit_roles = [r for r in roles if kw in r]
            hit_tabs = [t for t in tabs if kw in t]
        if hit_roles and hit_tabs:
            overlap.append(kw)
            evidence.append({"token": kw, "roles": hit_roles, "tabs": hit_tabs})

    aligned = bool(overlap)
    # R23-5：字面无交集 ≠ 池子不可用（宽标签"技术总监/技术合伙人"下常有对口职位）。
    # 判定改为"提示器"：先给旁证方法，不直接建议用户停手改期望。
    suggestion = ("存在方向词交集，仍建议语义复核具体方向是否一致" if aligned else
                  "标签字面无交集，但这只是**词面**判断，不代表池子不可用（宽标签下常有好岗）。"
                  "建议先跑 `jobs --all-tabs` 看 direction_match 命中率："
                  "≥30% 即可继续投递；持续偏低再考虑调整 Boss「期望管理」")
    return {"aligned": aligned, "overlap": overlap, "evidence": evidence,
            "suggestion": suggestion}


def parse_salary(salary: str):
    """解析薪资字符串 → (月薪下限k, 月数)。解析不了返回 (None, None)。

    "30-50K·16薪" → (30.0, 16)；"80-130K" → (80.0, 12)；
    面议/元/天/元/小时 → (None, None)，不硬杀，交给 AI 软判断。
    """
    if not salary:
        return None, None
    m = re.search(r"(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)K", salary.upper())
    if not m:
        return None, None
    lo = float(m.group(1))
    mm = re.search(r"(\d+)\s*薪", salary)
    months = int(mm.group(1)) if mm else 12
    return lo, months


def effective_monthly(lo: float, months: int) -> float:
    """折算成 12 薪的有效月薪下限。"""
    return lo * months / 12.0


def hard_filter(job, cfg: dict):
    """返回 (是否通过, 拒绝原因)。通过=True 继续后续 AI 评估。"""
    f = cfg.get("filter", {})
    text = " ".join(filter(None, [job.title, *job.tags]))
    text_lower = text.lower()

    # 1. 城市：白名单硬过滤；职位名/标签含「远程/在家/remote」放行
    cities = f.get("allowed_cities") or []
    if cities and job.location:
        if not any(c in job.location for c in cities):
            is_remote = any(k in text_lower for k in ("远程", "在家", "remote"))
            if not is_remote:
                city = (job.location.split() or [job.location])[0]
                return False, f"城市不符（{city}）"

    # 2. 薪资区间带：按折算12薪的月薪下限比较（月薪×月数÷12）。
    #    上限可为 null = 不限高。注意：上限是"刻意收紧的召回过滤"——
    #    追求匹配精确可设上限（如 60），追求召回设 null。
    band = f.get("salary_band_k") or []
    if len(band) == 2:
        lo, months = parse_salary(job.salary)
        if lo is not None:
            eff = effective_monthly(lo, months)
            if eff < band[0]:
                return False, f"薪资低于下限（{job.salary} = 折算{eff:.0f}K/月）"
            if band[1] is not None and eff > band[1]:
                return False, f"薪资超预期（{job.salary} = 折算{eff:.0f}K/月）"

    # 3. 黑名单关键词
    bl = f.get("blacklist_keywords") or []
    full = " ".join(filter(None, [job.title, job.company, *job.tags, *job.welfare]))
    for kw in bl:
        if kw in full:
            return False, f"黑名单（{kw}）"

    return True, ""
