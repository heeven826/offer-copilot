"""boss CLI —— 供 AI Agent（DSH / Claude Code 等 skill 形态）或人类直接调用的原子命令。

设计原则：
- 每个子命令 = 一个原子动作，输出 JSON（{"ok":..., "data"/"error"}）
- 护栏内置且跨进程持久化（SQLite state 表）：
  * greet: 60-70s 消息间隔（自动等待）、每日上限、去重、300字符长度限制
  * detail: 30-40s 查看间隔
  * 判断（打分/话术/回复措辞）不在 CLI 里 —— 那是上层模型的事（见 SKILL.md）

用法：
  python cli.py status --json
  python cli.py jobs
  python cli.py detail <encryptJobId>
  python cli.py greet <encryptJobId> --msg "您好…" [--title "职位名"]
  python cli.py inbox [--max 5]
  python cli.py reply <HR姓名> --msg "…" [--brand 公司名]
  python cli.py resume <HR姓名> [--brand 公司名]
  python cli.py archive [--top 20] [--status greeted] [--query 关键词]
  python cli.py setup
  python cli.py run [--dry-run] [--max-greets N] [--max-jobs N]   # 自主模式(兼容旧 boss.bat 参数)
"""
import argparse
import json
import random
import sys
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from boss_browser.actions import Communicator
from boss_browser.browser import BossBrowser
from boss_browser.expect_page import ExpectNavigator
from boss_browser.inbox import InboxProcessor
from filters import hard_filter, parse_salary, effective_monthly
from safety import DailyCap
from storage.db import Database
from storage.models import Application, Job


def out(data=None, error=None):
    """统一 JSON 输出后退出。"""
    if error is not None:
        print(json.dumps({"ok": False, "error": error}, ensure_ascii=False, indent=1))
        sys.exit(1)
    print(json.dumps({"ok": True, "data": data}, ensure_ascii=False, indent=1))
    sys.exit(0)


def load_cfg():
    return yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))


def wait_pace(db, key, rng, label):
    """跨进程节奏：距上次 < 随机区间值则自动等待。"""
    last = float(db.get_state(key) or 0)
    need = random.uniform(*rng)
    remain = need - (time.time() - last)
    if remain > 0:
        print(f"[护栏] {label}节奏等待 {remain:.0f}s…", file=sys.stderr)
        time.sleep(remain)


def connect(cfg, db):
    """连接 Chrome 并确认登录。"""
    b = BossBrowser(cfg, ROOT)
    tab = b.connect()
    if not b.ensure_login(timeout=120):
        out(error="not_logged_in（请运行 setup 扫码登录）")
    return tab


# ---------------- 子命令 ----------------

def cmd_status(args):
    cfg = load_cfg()
    db = Database(ROOT / "data" / "boss.db")
    tab = connect(cfg, db)
    en = ExpectNavigator(tab, cfg)
    en.open()
    tabs = en.expect_tabs()
    rate = cfg.get("rate", {})
    cap = DailyCap(rate.get("daily_greet_limit", 40), db)
    out({
        "login": True,
        "expect_tabs": [(t.text or "").split("\n")[0] for t in tabs],
        "today_greeted": cap.used(),
        "daily_limit": cap.limit,
        "last_greet_ts": float(db.get_state("last_greet_ts") or 0),
        "filters": cfg.get("filter", {}),
        "rate": {k: rate.get(k) for k in
                 ("between_greets", "between_details", "between_pages")},
    })


def cmd_jobs(args):
    cfg = load_cfg()
    db = Database(ROOT / "data" / "boss.db")
    tab = connect(cfg, db)
    en = ExpectNavigator(tab, cfg)
    en.open()
    jobs, page, has_more = en.capture_jobs()
    en.close()
    data = []
    for raw in jobs:
        lo, months = parse_salary(raw.get("salaryDesc") or "")
        eff = effective_monthly(lo, months) if lo else None
        j = Job(job_id=raw.get("encryptJobId", ""), title=raw.get("jobName", ""),
                salary=raw.get("salaryDesc", ""), company=raw.get("brandName", ""))
        ok, why = hard_filter(j, cfg)
        data.append({
            "job_id": raw.get("encryptJobId", ""),
            "title": raw.get("jobName", ""),
            "salary": raw.get("salaryDesc", ""),
            "effective_monthly_k": round(eff, 1) if eff else None,
            "company": raw.get("brandName", ""),
            "company_info": " · ".join(filter(None, [
                raw.get("brandScaleName"), raw.get("brandStageName"),
                raw.get("brandIndustry")])),
            "hr": f"{raw.get('bossName','')}（{raw.get('bossTitle','')}）",
            "location": " ".join(filter(None, [
                raw.get("cityName"), raw.get("areaDistrict"),
                raw.get("businessDistrict")])),
            "experience": raw.get("jobExperience", ""),
            "education": raw.get("jobDegree", ""),
            "tags": raw.get("skills") or [],
            "welfare": raw.get("welfareList") or [],
            "link": f"https://www.zhipin.com/job_detail/{raw.get('encryptJobId','')}.html",
            "filter_ok": ok,
            "filter_reason": why,
            "processed": db.is_done(raw.get("encryptJobId", "")),
        })
    out({"page": page, "has_more": has_more, "count": len(data), "jobs": data})


def cmd_detail(args):
    cfg = load_cfg()
    db = Database(ROOT / "data" / "boss.db")
    tab = connect(cfg, db)
    comm = Communicator(tab, cfg, db)
    job = Job(job_id=args.job_id, title=args.title or "")
    jd = comm.open_detail(job)      # 内部自带 30-40s 跨进程节奏
    if jd is False:
        out(error="job_id_invalid")
    # JD 回写档案库（沉淀）
    if jd and args.title:
        job.job_desc = jd
        db.update_job_desc(job)
    out({"job_id": args.job_id, "job_desc": jd,
         "link": f"https://www.zhipin.com/job_detail/{args.job_id}.html"})


def cmd_greet(args):
    cfg = load_cfg()
    db = Database(ROOT / "data" / "boss.db")
    rate = cfg.get("rate", {})

    # ---- 护栏（全部在工具层强制，拒绝违规调用）----
    if not args.msg or not args.msg.strip():
        out(error="msg_required")
    if len(args.msg) > 300:
        out(error=f"msg_too_long({len(args.msg)}>300)")
    cap = DailyCap(rate.get("daily_greet_limit", 40), db)
    if not cap.allow():
        out(error=f"daily_cap_reached({cap.limit})")
    if db.is_done(args.job_id):
        out(error="already_done（该职位已处理过，拒绝重复打招呼）")
    wait_pace(db, "last_greet_ts", rate.get("between_greets", [60, 70]), "打招呼")

    tab = connect(cfg, db)
    comm = Communicator(tab, cfg, db)
    job = Job(job_id=args.job_id, title=args.title or "")
    ok = comm.greet(args.msg, job=job)      # 内部含详情节奏/风控检查/拟人操作
    if not ok:
        db.mark_job(args.job_id, "failed")
        db.record(Application(args.job_id, args.title or "", "",
                              status="failed", greeting=args.msg))
        out(error="greet_failed（按钮缺失/职位关闭/风控，详见日志）")
    db.mark_job(args.job_id, "greeted")
    db.record(Application(args.job_id, args.title or "", "",
                          status="greeted", greeting=args.msg))
    out({"job_id": args.job_id, "sent": True, "message": args.msg,
         "today_greeted": cap.used()})


def cmd_inbox(args):
    cfg = load_cfg()
    db = Database(ROOT / "data" / "boss.db")
    tab = connect(cfg, db)
    inbox = InboxProcessor(tab, cfg, db, {})
    result = inbox.collect_unread(max_conv=args.max)
    out({"count": len(result), "conversations": result})


def cmd_reply(args):
    cfg = load_cfg()
    db = Database(ROOT / "data" / "boss.db")
    rate = cfg.get("rate", {})
    if not args.msg or len(args.msg) > 300:
        out(error="msg_required_or_too_long(>300)")
    wait_pace(db, "last_greet_ts", rate.get("between_greets", [60, 70]), "消息")
    tab = connect(cfg, db)
    inbox = InboxProcessor(tab, cfg, db, {})
    if not inbox.find_and_open(args.name, args.brand or ""):
        out(error=f"conversation_not_found({args.name})")
    if not inbox._reply(args.msg):
        out(error="reply_failed")
    from datetime import datetime
    db.set_state("last_greet_ts", str(time.time()))
    db.record_reply(args.name, args.brand or "", "reply", args.msg)
    out({"to": args.name, "sent": True, "message": args.msg})


def cmd_resume(args):
    cfg = load_cfg()
    db = Database(ROOT / "data" / "boss.db")
    tab = connect(cfg, db)
    inbox = InboxProcessor(tab, cfg, db, {})
    if not inbox.find_and_open(args.name, args.brand or ""):
        out(error=f"conversation_not_found({args.name})")
    if not inbox._send_resume():
        out(error="resume_failed（按钮不可用：需双方回复过；或未找到）")
    db.record_reply(args.name, args.brand or "", "resume", "")
    out({"to": args.name, "resume_sent": True})


def cmd_archive(args):
    db = Database(ROOT / "data" / "boss.db")
    sql = ("SELECT j.*, (SELECT MAX(score) FROM applications a WHERE a.job_id=j.job_id) AS score "
           "FROM jobs j")
    cond, params = [], []
    if args.status:
        cond.append("j.status=?")
        params.append(args.status)
    if args.query:
        cond.append("(j.title LIKE ? OR j.company LIKE ? OR j.raw_json LIKE ?)")
        params += [f"%{args.query}%"] * 3
    if cond:
        sql += " WHERE " + " AND ".join(cond)
    sql += " ORDER BY score IS NULL, score DESC LIMIT ?"
    params.append(args.top)
    rows = db.conn.execute(sql, params).fetchall()
    data = []
    for r in rows:
        try:
            raw = json.loads(r["raw_json"] or "{}")
        except Exception:
            raw = {}
        data.append({
            "job_id": r["job_id"], "title": r["title"], "salary": raw.get("salary", ""),
            "company": raw.get("company", ""), "location": raw.get("location", ""),
            "tags": raw.get("tags", []), "score": r["score"], "status": r["status"],
            "job_desc": (raw.get("job_desc", "") or "")[:400], "link": r["link"],
            "first_seen": r["first_seen"],
        })
    out({"count": len(data), "jobs": data})


def cmd_setup(args):
    cfg = load_cfg()
    db = Database(ROOT / "data" / "boss.db")
    b = BossBrowser(cfg, ROOT)
    tab = b.connect()
    print("等待登录（如浏览器显示登录页请扫码，最长等5分钟）…", file=sys.stderr)
    if not b.ensure_login(timeout=300):
        out(error="login_timeout")
    en = ExpectNavigator(tab, cfg)
    en.open()
    tabs = en.expect_tabs()
    en.close()
    out({
        "login": True,
        "expect_tabs": [(t.text or "").split("\n")[0] for t in tabs],
        "hint": "期望职位请在 Boss直聘 App/网页 的「期望管理」中维护；"
                "城市/薪资带在 config.yaml 的 filter 段调整。",
    })


def cmd_run(args):
    """自主模式（原有完整流程：粗筛→精筛→打招呼→收件箱）。"""
    from main import run as run_auto
    run_auto(argparse.Namespace(
        dry_run=args.dry_run, probe=None, max_jobs=args.max_jobs,
        max_greets=args.max_greets))
    out({"mode": "autonomous_run_finished"})


# ---------------- 入口 ----------------

def main():
    argv = sys.argv[1:]
    if argv and argv[0].startswith("-"):
        argv = ["run"] + argv          # 兼容旧用法: python cli.py --dry-run

    p = argparse.ArgumentParser(description="Boss直聘 AI 求职助手 CLI（agent 友好）")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="登录态/期望职位/配额/护栏配置")
    sub.add_parser("jobs", help="当前期望页职位列表（含硬过滤与去重标注）")
    d = sub.add_parser("detail", help="打开职位详情抓JD（含30-40s节奏）")
    d.add_argument("job_id")
    d.add_argument("--title", default="")
    g = sub.add_parser("greet", help="发送打招呼（护栏：节奏/日上限/去重/长度）")
    g.add_argument("job_id")
    g.add_argument("--msg", required=True)
    g.add_argument("--title", default="")
    i = sub.add_parser("inbox", help="读取未读会话与消息（只读）")
    i.add_argument("--max", type=int, default=5)
    r = sub.add_parser("reply", help="回复指定会话")
    r.add_argument("name")
    r.add_argument("--msg", required=True)
    r.add_argument("--brand", default="")
    s = sub.add_parser("resume", help="向指定会话发送在线简历")
    s.add_argument("name")
    s.add_argument("--brand", default="")
    a = sub.add_parser("archive", help="查询职位沉淀档案")
    a.add_argument("--top", type=int, default=20)
    a.add_argument("--status", default="")
    a.add_argument("--query", default="")
    sub.add_parser("setup", help="首次配置：扫码登录+读取期望职位")
    r2 = sub.add_parser("run", help="自主模式（完整自动流程）")
    r2.add_argument("--dry-run", action="store_true")
    r2.add_argument("--max-jobs", type=int, default=0)
    r2.add_argument("--max-greets", type=int, default=0)

    args = p.parse_args(argv)
    {"status": cmd_status, "jobs": cmd_jobs, "detail": cmd_detail,
     "greet": cmd_greet, "inbox": cmd_inbox, "reply": cmd_reply,
     "resume": cmd_resume, "archive": cmd_archive, "setup": cmd_setup,
     "run": cmd_run}[args.cmd](args)


if __name__ == "__main__":
    main()
