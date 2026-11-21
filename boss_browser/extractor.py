"""字段提取 —— 这是下一步我们俩一起做的部分。

三类入口已备好：
- parse_job_card():  从列表页卡片提取概要信息（不进详情页，快，用于初筛）
- parse_job_detail(): 进详情页提取职位描述等完整信息（慢，只对初筛通过的职位做）
- dump_sample():     --probe 模式下把卡片/页面 HTML 存到 samples/，
                     我们对着样本补齐每个字段的真实选择器。
"""
import time
from pathlib import Path

from loguru import logger

from storage.models import Job


class Extractor:
    def __init__(self, project_root: Path):
        self.samples_dir = project_root / "samples"
        self.samples_dir.mkdir(exist_ok=True)

    # ---------- API 数据（主通道，明文、无字体加密问题） ----------

    def parse_api_job(self, raw: dict) -> Job:
        """把 /recommend/job/list.json 的单条职位数据转成 Job。

        字段映射（2026-11 实测）：
        jobName→title  salaryDesc→salary  brandName→company
        brandScaleName/StageName/Industry→company_info
        bossName/bossTitle→hr  skills→tags  jobExperience/jobDegree→要求
        encryptJobId→job_id（去重键）
        """
        company_info = " · ".join(filter(None, [
            raw.get("brandScaleName"), raw.get("brandStageName"), raw.get("brandIndustry")]))
        location = " ".join(filter(None, [
            raw.get("cityName"), raw.get("areaDistrict"), raw.get("businessDistrict")]))
        return Job(
            job_id=raw.get("encryptJobId", ""),
            title=raw.get("jobName", ""),
            salary=raw.get("salaryDesc", ""),
            company=raw.get("brandName", ""),
            company_info=company_info,
            hr_name=raw.get("bossName", ""),
            hr_title=raw.get("bossTitle", ""),
            tags=raw.get("skills") or [],
            welfare=raw.get("welfareList") or [],
            location=location,
            experience=raw.get("jobExperience", ""),
            education=raw.get("jobDegree", ""),
            link=f"https://www.zhipin.com/job_detail/{raw.get('encryptJobId', '')}.html",
            raw_text=raw.get("jobName", ""),
        )

    # ---------- 列表页 ----------

    def parse_job_card(self, card) -> Job | None:
        """从列表页卡片提取字段。

        当前为兜底实现：只抓整卡文本和链接（保证流程能跑通），
        TODO(字段提取): 对着 samples/ 样本补齐逐字段选择器，例如：
            job.title    = card.ele("css:.job-name").text
            job.salary   = card.ele("css:.salary").text
            job.company  = card.ele("css:.company-name").text
            job.tags     = [t.text for t in card.eles("css:.tag-list li")]
            job.job_id   = 从链接参数提取 (securityId / encryptJobId)
        """
        try:
            job = Job()
            job.raw_text = (card.text or "").strip()
            try:
                job.link = card.link or ""
            except Exception:
                job.link = ""
            return job if job.raw_text else None
        except Exception as e:
            logger.debug(f"解析卡片失败: {e}")
            return None

    # ---------- 详情页 ----------

    def parse_job_detail(self, tab) -> dict:
        """从当前职位详情页提取完整信息。

        TODO(字段提取): 校准后启用，预期结构：
            {"job_desc": 职位描述全文, "company_info": 公司介绍, ...}
        """
        data = {"job_desc": ""}
        # TODO: sec = tab.ele("css:.job-sec-text", timeout=3)
        #       if sec: data["job_desc"] = sec.text
        return data

    # ---------- 采样 ----------

    def dump_sample(self, element, prefix: str, idx: int) -> Path:
        """把元素/页面 HTML 存档，供离线分析选择器（--probe 模式用）。"""
        path = self.samples_dir / f"{prefix}_{int(time.time())}_{idx:02d}.html"
        path.write_text(element.html or "", encoding="utf-8")
        logger.info(f"已保存样本: {path.name}")
        return path
