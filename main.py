"""Boss直聘 AI 求职助手 —— 入口。

流程：连接本机 Chrome → 确认登录 → 遍历期望职位标签（默认）
      → 监听接口拿明文职位数据 → 本地去重/粗筛 → AI 评估 → 打招呼(待联调) → 记录入库

常用命令：
  python main.py --probe 5      # 采样模式：解析前 5 个职位打印并存档，不做任何操作
  python main.py --dry-run      # 演练模式：跑完整 AI 评估但不发送任何消息
  python main.py --max-jobs 10  # 本次最多处理 10 个职位
  python main.py                # 正式运行（打招呼联调完成后生效）
"""
import argparse
import json
import os
import random
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv
from loguru import logger

PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

from ai.greeting import generate_greeting
from ai.judge import evaluate_job
from boss_browser.actions import Communicator, GREET_READY
from boss_browser.browser import BossBrowser
from boss_browser.expect_page import ExpectNavigator
from boss_browser.extractor import Extractor
from boss_browser.human import sleep_range
from boss_browser.risk import RiskControlError
from filters import hard_filter
from safety import DailyCap, RateLimiter
from storage.db import Database
from storage.models import Application, Job


class StopRun(Exception):
    """用于干净地结束整个运行（达到上限/用户要求停止）。"""


def setup_logging(level: str):
    log_dir = PROJECT_ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    logger.remove()
    logger.add(sys.stderr, level=level)
    logger.add(log_dir / "boss_{time:YYYY-MM-DD}.log", level="DEBUG",
               rotation="1 day", retention="14 days", encoding="utf-8")


def handle_risk(cfg: dict) -> bool:
    """风控处理：默认暂停等人工，返回是否继续运行。"""
    if not cfg.get("rate", {}).get("pause_on_risk", True):
        logger.error("触发风控且配置为不暂停，停止运行")
        return False
    try:
        input("\n⚠ 触发安全验证。请在浏览器中手动完成验证，然后回到这里按回车继续（Ctrl+C 退出）…")
        return True
    except (KeyboardInterrupt, EOFError):
        return False


# ---------------- 核心：处理单个职位（数据驱动，与来源无关） ----------------

def process_job(job: Job, ctx: dict) -> str:
    """处理单个职位，返回结果（主循环据此决定是否作废旧批次）：
    greeted=已真实打招呼(列表已刷新) / would_greet=演练本应投(页面未变) /
    skipped=拒绝 / seen=重复(零AI成本) / failed=失败 / error=AI暂时性失败
    """
    stats = ctx["stats"]
    if ctx["args"].max_jobs and stats["seen"] >= ctx["args"].max_jobs:
        raise StopRun("达到 --max-jobs 上限")
    stats["seen"] += 1
    logger.info(f"[{stats['seen']}] {job.brief()}")

    if not job.job_id:
        job.job_id = "raw::" + (job.title or job.raw_text[:60])

    # 全量沉淀：所有职位进档案库（简历优化/面试备用）
    ctx["db"].save_job(job)
    if ctx["db"].is_done(job.job_id):
        logger.debug("已有终态（跳过/已打招呼），不再处理")
        return "seen"

    # 硬规则前置（城市/薪资下限/黑名单 —— 零成本、确定性拦截）
    ok, why = hard_filter(job, ctx["cfg"])
    if not ok:
        logger.info(f"硬规则拦截: {why}")
        ctx["db"].mark_job(job.job_id, "skipped")
        ctx["db"].record(Application(job.job_id, job.title, job.company,
                                     status="skipped", reason=why))
        stats["skipped"] += 1
        return "skipped"

    # ---- 阶段1：AI 粗筛（仅列表信息）----
    try:
        jr = evaluate_job(job, ctx["profile"], ctx["stage1_score"], stage="粗筛")
    except Exception as e:
        logger.warning(f"AI 粗筛失败（检查 .env 配置），保守跳过: {e}")
        return "error"
    stats["evaluated"] += 1

    if not jr.should_apply:
        ctx["db"].mark_job(job.job_id, "skipped")
        ctx["db"].record(Application(job.job_id, job.title, job.company,
                                     status="skipped", score=jr.score, reason=jr.reasons))
        stats["skipped"] += 1
        return "skipped"

    fallback = ctx["cfg"].get("apply", {}).get(
        "default_greeting", "您好，希望有机会进一步沟通！")

    if ctx["args"].dry_run:
        greeting = generate_greeting(job, ctx["profile"], fallback)
        logger.success(f"[演练] 粗筛通过({jr.score}分): {job.brief()} | 话术: {greeting}")
        ctx["db"].record(Application(job.job_id, job.title, job.company, status="dry_run",
                                     score=jr.score, reason=jr.reasons, greeting=greeting))
        return "would_greet"

    # 打招呼（联调未完成时仅记录，不执行）
    if not GREET_READY:
        stats["pending"] += 1
        greeting = generate_greeting(job, ctx["profile"], fallback)
        ctx["db"].record(Application(job.job_id, job.title, job.company, status="evaluated",
                                     score=jr.score, reason=jr.reasons, greeting=greeting))
        logger.info(f"[待联调] 打招呼功能未联调，仅记录: {job.brief()}")
        return "would_greet"

    if not ctx["cap"].allow():
        raise StopRun("达到每日上限")

    # ---- 打开详情：抓 JD（精评依据 + 话术语言 + 沉淀档案）----
    jd = ctx["comm"].open_detail(job)
    if jd is False:
        ctx["db"].mark_job(job.job_id, "failed")
        ctx["db"].record(Application(job.job_id, job.title, job.company, status="failed",
                                     score=jr.score, reason="未找到职位卡片"))
        return "failed"
    if jd:
        job.job_desc = jd
        ctx["db"].update_job_desc(job)

    # ---- 阶段2：JD 精评（最终决定）----
    try:
        jr2 = evaluate_job(job, ctx["profile"], ctx["min_score"], stage="精评")
    except Exception as e:
        # 精评失败是暂时性错误（API 抖动），不标终态，下次运行重试
        logger.warning(f"AI 精评失败，本次跳过（下次重试）: {e}")
        return "error"
    stats["evaluated"] += 1

    if not jr2.should_apply:
        ctx["db"].mark_job(job.job_id, "skipped")
        ctx["db"].record(Application(job.job_id, job.title, job.company, status="skipped",
                                     score=jr2.score, reason=f"精评: {jr2.reasons}"))
        stats["skipped"] += 1
        return "skipped"

    # ---- 生成话术（JD 英文 → 英文话术）并发送 ----
    greeting = generate_greeting(job, ctx["profile"], fallback)

    res = ctx["comm"].greet(greeting, job=job, detail_open=True)
    if not res.get("ok") or not res.get("verified"):   # R25-2：未验证=失败，不落 greeted
        ctx["db"].mark_job(job.job_id, "failed")
        ctx["db"].record(Application(job.job_id, job.title, job.company, status="failed",
                                     score=jr2.score,
                                     reason=f"[{res.get('stage')}] {res.get('note', '')}"[:80],
                                     greeting=greeting))
        # 已在会话中的不算失败——标记 skipped 避免反复尝试
        if res.get("stage") == "already_in_conversation":
            ctx["db"].mark_job(job.job_id, "skipped")
        return "failed"
    status = "greeted"
    if ctx["cfg"].get("apply", {}).get("send_resume_card"):
        if ctx["comm"].send_resume_card():
            status = "resume_sent"
    ctx["db"].mark_job(job.job_id, status)
    ctx["db"].record(Application(job.job_id, job.title, job.company, status=status,
                                 score=jr2.score, reason=jr2.reasons, greeting=greeting))
    stats["greeted"] += 1
    ctx["limiter"].wait_greet()
    # 拟人长休息：像人一样偶尔停下来干点别的
    rate = ctx["cfg"].get("rate", {})
    if random.random() < rate.get("rest_chance", 0.25):
        sleep_range(*rate.get("rest_range", [15, 45]), "拟人长休息")
    if ctx["args"].max_greets and stats["greeted"] >= ctx["args"].max_greets:
        raise StopRun("达到 --max-greets 上限")
    return "greeted"


# ---------------- 职位来源：期望职位推荐页（默认） ----------------

def run_expect_mode(ctx: dict):
    """期望页主循环（批次跟随页面实时状态）。

    每轮从接口捕获"当前页面真实列表"的一批职位，逐个处理
    （硬过滤 → 粗筛 → 打开详情精筛 → 打招呼）。
    一旦发生真实打招呼，返回后列表必然刷新轮换 —— 立即作废本批次
    剩余职位（数据已过期、卡片已消失），重新捕获新列表；
    重复出现的职位由去重库零成本拦截，不做无用功。
    一轮下来没有新打招呼 → 该标签处理完毕，切下一个。
    """
    en = ExpectNavigator(ctx["tab"], ctx["cfg"])
    try:
        en.open()
        if ctx["args"].probe:
            run_probe(en, ctx)
            return
        n_tabs = len(en.expect_tabs())
        max_pages = ctx["cfg"].get("search", {}).get("expect_pages", 2)
        for i in range(n_tabs):
            try:
                ctx["comm"].set_expect_tab(i)
                if i > 0:
                    en.click_tab_by_index(i)         # 触发该标签的列表数据
                fresh, page_no, has_more = en.capture_jobs()
                pages_seen = max(page_no, 1)
                rounds = 0
                while fresh and rounds < 30:
                    greeted = False
                    for raw in fresh:
                        outcome = process_job(ctx["extractor"].parse_api_job(raw), ctx)
                        if outcome == "greeted":     # 列表已刷新轮换，本批次作废
                            greeted = True
                            break
                    rounds += 1
                    if greeted:
                        # 打招呼返回触发的刷新包通常已在队列里；没有就主动点标签
                        fresh, page_no, has_more = en.capture_jobs(timeout=6)
                        if not fresh:
                            en.click_tab_by_index(i)
                            fresh, page_no, has_more = en.capture_jobs()
                        pages_seen = 1               # 刷新后回到第1页
                        continue
                    # 本页无新打招呼：还有下一页且未超页数上限 → 翻页再来
                    # （dry-run 不翻页，快速过一遍即可）
                    if (not ctx["args"].dry_run and has_more
                            and pages_seen < max_pages):
                        logger.info(f"期望标签[{i}] 第{pages_seen}页无新打招呼，翻页…")
                        jobs2, p2, hm2 = en.load_next_page(pages_seen)
                        if jobs2:
                            fresh = jobs2
                            page_no = p2
                            has_more = hm2
                            pages_seen = p2
                            continue
                    logger.info(f"期望标签[{i}] 处理完毕（{pages_seen}页/{rounds}轮）")
                    break
            except StopRun:
                raise                               # 上限停止要正常传播
            except Exception as e:
                logger.exception(f"第 {i + 1} 个期望标签处理失败，继续下一个: {e}")
            ctx["limiter"].wait_page()
    finally:
        en.close()


def run_probe(en: ExpectNavigator, ctx: dict):
    """采样模式：解析前 N 个职位，打印并存档 samples/probe_expect.json。"""
    n = ctx["args"].probe
    logger.info(f"采样模式：解析前 {n} 个职位")
    tabs = en.expect_tabs()
    raws = []
    for i, t in enumerate(tabs):
        raws.extend(en.iter_jobs_of_tab(t, click=(i > 0), max_pages=1))
        if len(raws) >= n:
            break
        sleep_range(2, 4, "采样切标签")
    parsed = [ctx["extractor"].parse_api_job(r) for r in raws[:n]]
    out = ctx["extractor"].samples_dir / "probe_expect.json"
    out.write_text(json.dumps([p.to_dict() for p in parsed], ensure_ascii=False, indent=1),
                   encoding="utf-8")
    for p in parsed:
        print(f"  {p.title} | {p.salary} | {p.company} | {p.company_info} | "
              f"{p.hr_name}({p.hr_title}) | {p.location} | {p.tags}")
    logger.success(f"采样完成，已存 {out}")


# ---------------- 职位来源：关键词搜索（后续支持） ----------------

def run_keyword_mode(ctx: dict):
    from boss_browser.navigator import JobListNavigator
    nav = JobListNavigator(ctx["tab"], ctx["cfg"])
    if ctx["args"].probe:
        logger.warning("keyword 模式采样暂未迁移，请用 expect 模式")
        return
    for keyword in ctx["cfg"]["search"]["keywords"]:
        nav.open_search(keyword)
        for _page in range(ctx["cfg"]["search"].get("max_pages", 1)):
            for card in nav.job_cards():
                job = ctx["extractor"].parse_job_card(card)
                if job:
                    process_job(job, ctx)
            if not nav.next_page():
                break
            ctx["limiter"].wait_page()


# ---------------- 入口 ----------------

def run(args):
    load_dotenv(PROJECT_ROOT / ".env")
    cfg = yaml.safe_load((PROJECT_ROOT / "config.yaml").read_text(encoding="utf-8"))
    profile = yaml.safe_load((PROJECT_ROOT / "profile.yaml").read_text(encoding="utf-8"))
    setup_logging(os.getenv("LOG_LEVEL", "INFO"))
    mode = "采样" if args.probe else "演练(dry-run)" if args.dry_run else "正式"
    logger.info(f"模式: {mode} | 来源: {cfg['search'].get('source', 'expect')}")

    db = Database(PROJECT_ROOT / "data" / "boss.db")
    cap = DailyCap(cfg.get("rate", {}).get("daily_greet_limit", 40), db)
    # R25-5：自动运行可观测性——开始即落一条 audit（睡眠漏跑 vs 跑了失败可区分）
    db.audit("run", outcome="start", mode=mode, quota_used=cap.used(), limit=cap.limit)
    logger.info(f"今日已打招呼 {cap.used()}/{cap.limit} | 打招呼联调: "
                f"{'已就绪' if GREET_READY else '未完成(仅评估)'}")

    browser = BossBrowser(cfg, PROJECT_ROOT)
    tab = browser.connect()
    if not browser.ensure_login():
        logger.error("未能确认登录状态，退出。请先手动登录一次")
        return

    ctx = {
        "args": args, "cfg": cfg, "profile": profile, "db": db, "tab": tab,
        "extractor": Extractor(PROJECT_ROOT), "comm": Communicator(tab, cfg, db),
        "limiter": RateLimiter(cfg.get("rate", {})), "cap": cap,
        "min_score": cfg.get("ai", {}).get("min_score", 7),
        "stage1_score": cfg.get("ai", {}).get("stage1_score", 6),
        "stats": {"seen": 0, "evaluated": 0, "greeted": 0, "skipped": 0, "pending": 0},
    }

    try:
        if cfg["search"].get("source", "expect") == "expect":
            run_expect_mode(ctx)
        else:
            run_keyword_mode(ctx)

        # 打完招呼后：处理收件箱未读（回复 / 发简历）
        apply_cfg = cfg.get("apply", {})
        if apply_cfg.get("check_inbox", True) and not args.probe:
            from boss_browser.inbox import InboxProcessor
            inbox = InboxProcessor(tab, cfg, db, profile)
            st = inbox.run(max_conv=apply_cfg.get("inbox_max_conversations", 5),
                           send_enabled=apply_cfg.get("inbox_send", True))
            logger.info(f"收件箱小结: 回复 {st['replied']} | 发简历 {st['resume']} | "
                        f"跳过 {st['skipped']} | 失败 {st['failed']}")
    except StopRun as e:
        logger.warning(f"提前结束: {e}")
    except RiskControlError:
        if not handle_risk(cfg):
            logger.warning("触发风控，运行结束")
    except KeyboardInterrupt:
        logger.warning("用户中断")
    except Exception:
        # 兜底：任何未预期错误都要留全量堆栈到日志，而不是无声退出
        logger.exception("未预期的错误，运行终止")
    finally:
        s = ctx["stats"]
        logger.info(
            f"本次小结: 浏览 {s['seen']} | AI评估 {s['evaluated']} | 跳过 {s['skipped']} | "
            f"待打招呼 {s['pending']} | 已打招呼 {s['greeted']} | 历史统计 {db.stats()}")
        db.audit("run", outcome="end", seen=s["seen"], evaluated=s["evaluated"],
                 greeted=s["greeted"], skipped=s["skipped"], pending=s["pending"])
        logger.info("日志见 logs/，投递记录见 data/boss.db")


def main():
    p = argparse.ArgumentParser(description="Boss直聘 AI 求职助手")
    p.add_argument("--dry-run", action="store_true", help="演练模式：完整评估但不发送任何消息")
    p.add_argument("--probe", type=int, metavar="N", help="采样模式：解析前 N 个职位后退出")
    p.add_argument("--max-jobs", type=int, default=0, help="本次最多处理多少个职位（含跳过的，0=不限）")
    p.add_argument("--max-greets", type=int, default=0, help="最多实际发送 N 条打招呼后停止（0=不限）")
    run(p.parse_args())


if __name__ == "__main__":
    main()
