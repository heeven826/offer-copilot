"""数据模型：Job（职位）与 Application（投递记录）。"""
from dataclasses import asdict, dataclass, field
from datetime import datetime


@dataclass
class Job:
    """职位信息。字段清单先立起来，具体提取逻辑在字段提取阶段一起补。"""
    job_id: str = ""            # 职位唯一标识（去重用）
    title: str = ""             # 职位名
    salary: str = ""            # 薪资，如 "15-25K"
    company: str = ""           # 公司名
    company_info: str = ""      # 公司规模/行业，如 "500-999人 · 互联网"
    hr_name: str = ""           # HR 称呼，如 "李女士"
    hr_title: str = ""          # HR 职位，如 "招聘者"
    tags: list = field(default_factory=list)   # 技能标签
    welfare: list = field(default_factory=list)  # 福利标签，如 ["年终奖","五险一金"]
    location: str = ""          # 工作地点
    experience: str = ""        # 经验要求，如 "3-5年"
    education: str = ""         # 学历要求
    job_desc: str = ""          # 职位描述全文（详情页提取）
    link: str = ""              # 职位链接
    raw_text: str = ""          # 卡片原始文本（字段联调阶段分析用）

    def brief(self) -> str:
        return f"{self.title} | {self.salary} | {self.company}"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Application:
    """一次投递/打招呼记录。"""
    job_id: str
    title: str = ""
    company: str = ""
    status: str = "pending"     # greeted / resume_sent / skipped / dry_run / failed
    score: int = 0              # AI 打分 0-10
    reason: str = ""            # AI 理由 / 跳过原因
    greeting: str = ""          # 实际发送的打招呼话术
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def to_dict(self) -> dict:
        return asdict(self)
