"""本地硬过滤：城市、薪资区间带、黑名单 —— 确定性规则前置，不花 AI 调用。

设计原则：能用规则确定杀掉的，绝不交给 LLM 自由裁量（比如城市不符）。
薪资按「折算成 12 薪的有效月薪」比较：
    "40-70K·16薪" → 40×16/12 = 53.3K/月（等价 12 薪的 53.3K）
    "35-60K"      → 35×12/12 = 35K/月
"""
import re


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
