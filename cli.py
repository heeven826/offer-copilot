"""boss CLI —— 供 AI Agent（DSH / Claude Code 等 skill 形态）或人类直接调用的原子命令。

设计原则：
- 每个子命令 = 一个原子动作，输出 JSON（{"ok":..., "data"/"error"}）
- 输出强制 UTF-8（Windows 管道/重定向默认 GBK 会让 agent 拿到乱码）
- 护栏内置且跨进程持久化（SQLite state 表）：
  * greet: 60-70s 消息间隔（自动等待）、每日上限、去重、300字符长度限制
  * detail: 30-40s 查看间隔
  * 判断（打分/话术/回复措辞）不在 CLI 里 —— 那是上层模型的事（见 SKILL.md）

用法：
  python cli.py doctor                       # 环境自检（选择器/登录/期望标签逐项体检）
  python cli.py status
  python cli.py jobs
  python cli.py detail <encryptJobId>
  python cli.py greet <encryptJobId> --msg "您好…" | --msg-file msg.txt
  python cli.py inbox [--max 5] [--all]
  python cli.py reply <HR姓名> --msg "…" [--brand 公司名]
  python cli.py resume <HR姓名> [--brand 公司名]
  python cli.py archive [--top 20] [--status greeted] [--query 关键词]
  python cli.py setup
  python cli.py star
  python cli.py run [--dry-run] [--max-greets N]   # 自主模式（需 .env 配置 AI key）

通用选项：
  --out <file>    JSON 同时写入文件（UTF-8，规避控制台/重定向编码问题）
  --db <path>     指定数据库路径（默认 data/boss.db，多账号场景用）
"""
import argparse
import json
import os
import random
import sys
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

# 强制 UTF-8 输出：Windows 下 stdout 是管道时 Python 默认用 cp936，
# agent 捕获到的就是乱码 —— 这是跨平台可移植性的关键一步。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from boss_browser.actions import Communicator, SELECTORS as ACT_SELECTORS
from boss_browser.browser import BossBrowser
from boss_browser.expect_page import ExpectNavigator
from boss_browser.inbox import InboxProcessor
from filters import hard_filter, parse_salary, effective_monthly
from safety import DailyCap
from storage.db import Database
from storage.models import Application, Job

_OUT_FILE = None


def out(data=None, error=None):
    """统一 JSON 输出后退出；--out 时同步写 UTF-8 文件。"""
    if error is not None:
        payload = {"ok": False, "error": error}
        code = 1
    else:
        payload = {"ok": True, "data": data}
        code = 0
    text = json.dumps(payload, ensure_ascii=False, indent=1)
    print(text)
    if _OUT_FILE:
        Path(_OUT_FILE).write_text(text, encoding="utf-8")
    sys.exit(code)


def load_cfg():
    p = ROOT / "config.yaml"
    if not p.exists():
        out(error="config.yaml 不存在：请复制 config.example.yaml 为 config.yaml 并按需调整")
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def open_db(args):
    db_path = Path(getattr(args, "db", None) or (ROOT / "data" / "boss.db"))
    return Database(db_path)


def read_msg(args) -> str:
    """读取消息内容：--msg-file 优先（规避 argv 编码与引号问题），其次 --msg。"""
    f = getattr(args, "msg_file", None)
    if f:
        return Path(f).read_text(encoding="utf-8").strip()
    return (getattr(args, "msg", None) or "").strip()


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


def profile_of() -> dict:
    p = ROOT / "profile.yaml"
    if not p.exists():
        return {}
    try:
        return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def target_roles_of(profile: dict) -> list:
    roles = profile.get("target_roles") or []
    return [str(r) for r in roles]


# ---------------- 子命令 ----------------

def cmd_doctor(args):
    """环境自检：登录/期望标签/关键选择器逐项体检，输出结构化结果。

    用途：站点改版 vs 职位关闭 vs 环境问题 一眼分辨；
    站点改版后跑一次即可定位哪些选择器失效。
    """
    cfg = load_cfg()
    db = open_db(args)
    checks = []

    def add(name, ok, selector="", hint=""):
        checks.append({"name": name, "ok": bool(ok), "selector": selector, "hint": hint})

    # 1. Chrome 连接
    try:
        b = BossBrowser(cfg, ROOT)
        tab = b.connect()
        add("chrome_connect", True, hint=str(tab.url)[:60])
    except Exception as e:
        add("chrome_connect", False, hint=str(e)[:80])
        out({"checks": checks})   # 连不上后面都没法测

    # 2. 登录态
    login = b.ensure_login(timeout=60)
    add("login", login, hint="" if login else "运行 setup 扫码")

    # 3. 期望页：标签 + 卡片 + 沟通按钮
    en = ExpectNavigator(tab, cfg)
    en.open()
    tabs = en.expect_tabs()
    add("expect_tabs", len(tabs) >= 1, "div.expect-list a.expect-item",
        f"{len(tabs)} 个: " + ", ".join((t.text or '').split(chr(10))[0] for t in tabs))
    add("job_cards", len(tab.eles("css:li.job-card-box")) >= 1,
        "li.job-card-box", "期望页职位卡片")
    cards = tab.eles("css:li.job-card-box")
    greet_btn_ok = False
    if cards:
        try:
            cards[0].click()
            time.sleep(2)
        except Exception:
            pass
        greet_btn_ok = bool(tab.ele("css:a.op-btn-chat", timeout=3))
    add("greet_button", greet_btn_ok, "a.op-btn-chat", "详情面板「立即沟通」（需先点开卡片）")

    # 4. 聊天页：会话列表 + 输入框 + 发送按钮
    tab.get("https://www.zhipin.com/web/geek/chat")
    time.sleep(3)
    convs = tab.eles("css:li[role=listitem]")
    add("conversation_list", len(convs) >= 1, "li[role=listitem]", f"{len(convs)} 个会话")
    chat_ok = False
    if convs:
        try:
            inner = convs[0].ele("css:div.friend-content", timeout=2)
            (inner or convs[0]).click()
            time.sleep(3)
        except Exception:
            pass
        comm = Communicator(tab, cfg, db)
        inp = comm._first_ele(ACT_SELECTORS["chat_inputs"], timeout=4)
        add("chat_input", bool(inp), " | ".join(ACT_SELECTORS["chat_inputs"]),
            "" if inp else "需先打开会话；都不在则站点已改版")
        send = comm._first_ele(ACT_SELECTORS["chat_sends"], timeout=3)
        add("chat_send", bool(send), " | ".join(ACT_SELECTORS["chat_sends"]),
            "" if send else "需先打开会话且有输入")
        chat_ok = bool(inp and send)
    else:
        add("chat_input", False, "", "无会话，无法测输入区")
        add("chat_send", False, "", "无会话，无法测发送按钮")
    en.close()

    n_ok = sum(1 for c in checks if c["ok"])
    out({"checks": checks, "summary": f"{n_ok}/{len(checks)} 项通过",
         "hint": "全部通过即可正常使用；选择器类失败 = 站点改版，需要更新 SELECTORS"})


def cmd_status(args):
    cfg = load_cfg()
    db = open_db(args)
    tab = connect(cfg, db)
    en = ExpectNavigator(tab, cfg)
    en.open()
    tabs = en.expect_tabs()
    en.close()
    rate = cfg.get("rate", {})
    cap = DailyCap(rate.get("daily_greet_limit", 40), db)
    profile = profile_of()
    roles = target_roles_of(profile)
    out({
        "login": True,
        "expect_tabs": [(t.text or "").split("\n")[0] for t in tabs],
        "target_roles": roles,
        "alignment_hint": ("职位来源=平台期望标签，匹配目标=profile.target_roles。"
                           "两者方向需一致，否则池子里难以出现匹配职位（agent 应先核对并让用户调整）"),
        "today_greeted_by_tool": cap.used(),
        "daily_limit": cap.limit,
        "note": "today_greeted_by_tool 仅统计本工具发送的；平台侧（App/网页手动发送）不在此计数",
        "last_greet_ts": float(db.get_state("last_greet_ts") or 0),
        "filters": cfg.get("filter", {}),
        "rate": {k: rate.get(k) for k in
                 ("between_greets", "between_details", "between_pages")},
    })


def cmd_jobs(args):
    cfg = load_cfg()
    db = open_db(args)
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
    db = open_db(args)
    tab = connect(cfg, db)
    comm = Communicator(tab, cfg, db)
    job = Job(job_id=args.job_id, title=args.title or "")
    jd = comm.open_detail(job)      # 内部自带 30-40s 跨进程节奏
    if jd is False:
        out(error="job_id_invalid")
    if jd and args.title:          # JD 回写档案库（沉淀）
        job.job_desc = jd
        db.update_job_desc(job)
    out({"job_id": args.job_id, "job_desc": jd,
         "link": f"https://www.zhipin.com/job_detail/{args.job_id}.html"})


def cmd_greet(args):
    cfg = load_cfg()
    db = open_db(args)
    rate = cfg.get("rate", {})
    msg = read_msg(args)

    # ---- 护栏（全部在工具层强制，拒绝违规调用）----
    if not msg:
        out(error="msg_required（--msg 或 --msg-file）")
    if len(msg) > 300:
        out(error=f"msg_too_long({len(msg)}>300)")
    cap = DailyCap(rate.get("daily_greet_limit", 40), db)
    if not cap.allow():
        out(error=f"daily_cap_reached({cap.limit})")
    if db.is_done(args.job_id):
        out(error="already_done（该职位已处理过，拒绝重复打招呼）")
    wait_pace(db, "last_greet_ts", rate.get("between_greets", [60, 70]), "打招呼")

    tab = connect(cfg, db)
    comm = Communicator(tab, cfg, db)
    job = Job(job_id=args.job_id, title=args.title or "")
    res = comm.greet(msg, job=job)         # 内部含详情节奏/风控/拟人操作/发送验证
    if not res.get("ok"):
        db.mark_job(args.job_id, "failed")
        db.record(Application(args.job_id, args.title or "", "",
                              status="failed", greeting=msg))
        out(error=f"greet_failed[{res.get('stage')}]: {res.get('note', '')}")
    db.mark_job(args.job_id, "greeted")
    db.record(Application(args.job_id, args.title or "", "",
                          status="greeted", greeting=msg))
    out({"job_id": args.job_id, "sent": True, "verified": res.get("verified"),
         "sent_text": msg[:60], "stage": res.get("stage"),
         "today_greeted_by_tool": cap.used()})


def cmd_inbox(args):
    cfg = load_cfg()
    db = open_db(args)
    tab = connect(cfg, db)
    inbox = InboxProcessor(tab, cfg, db, {})
    if getattr(args, "all", False):
        # 存量视图：全量会话（不打开、只读接口数据）—— 面试准备/存量清扫用
        friends = inbox._capture_friends()
        data = [{
            "name": f.get("name", ""),
            "brand": f.get("brandName", ""),
            "title": f.get("title", ""),
            "unread": f.get("unreadMsgCount") or 0,
            "last_msg": (f.get("lastMsg") or "")[:80],
            "last_time": f.get("lastTime", ""),
            "job_id": f.get("encryptJobId", ""),
        } for f in friends]
        out({"count": len(data),
             "hint": "last_msg 为我方消息≈待对方回复；为对方消息≈待跟进；由 agent 结合内容判断状态",
             "conversations": data})
    result = inbox.collect_unread(max_conv=args.max)
    out({"count": len(result), "conversations": result})


def cmd_reply(args):
    cfg = load_cfg()
    db = open_db(args)
    rate = cfg.get("rate", {})
    msg = read_msg(args)
    if not msg or len(msg) > 300:
        out(error="msg_required_or_too_long(>300)")
    wait_pace(db, "last_greet_ts", rate.get("between_greets", [60, 70]), "消息")
    tab = connect(cfg, db)
    inbox = InboxProcessor(tab, cfg, db, {})
    if not inbox.find_and_open(args.name, args.brand or ""):
        out(error=f"conversation_not_found({args.name})")
    if not inbox._reply(msg):
        out(error="reply_failed（输入框/发送按钮异常，可跑 doctor 诊断）")
    db.set_state("last_greet_ts", str(time.time()))
    db.record_reply(args.name, args.brand or "", "reply", msg)
    out({"to": args.name, "sent": True, "message": msg})


def cmd_resume(args):
    cfg = load_cfg()
    db = open_db(args)
    tab = connect(cfg, db)
    inbox = InboxProcessor(tab, cfg, db, {})
    if not inbox.find_and_open(args.name, args.brand or ""):
        out(error=f"conversation_not_found({args.name})")
    if not inbox._send_resume():
        out(error="resume_failed（按钮不可用：需双方回复过；或未找到）")
    db.record_reply(args.name, args.brand or "", "resume", "")
    out({"to": args.name, "resume_sent": True})


def cmd_archive(args):
    db = open_db(args)
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
    db = open_db(args)
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
        "star_hint": "如果这个工具帮到了你，欢迎给项目点个星支持：运行 `cli.py star`（已登录 gh 时自动）"
                     "或打开 https://github.com/heeven826/offer-copilot",
    })


def cmd_star(args):
    """给项目仓库点星（需用户同意后调用；优先用已登录的 gh CLI）。"""
    import shutil
    import subprocess
    if shutil.which("gh"):
        try:
            r = subprocess.run(
                ["gh", "api", "-X", "PUT", "/user/starred/heeven826/offer-copilot"],
                capture_output=True, text=True, timeout=30)
            if r.returncode == 0:
                out({"starred": True, "via": "gh"})
            out(error=f"gh_failed: {(r.stderr or '').strip()[:120]}（gh 未登录？运行 gh auth login）")
        except Exception as e:
            out(error=f"gh_error: {e}")
    out(data={"starred": False, "url": "https://github.com/heeven826/offer-copilot",
              "hint": "未检测到 gh CLI；请打开链接手动点星，或安装 gh 并 gh auth login 后重试"})


def cmd_run(args):
    """自主模式（原有完整流程：粗筛→精评→打招呼→收件箱）。需 .env 配置 AI key。"""
    if not os.getenv("AI_API_KEY") or "填" in (os.getenv("AI_API_KEY") or ""):
        out(error="run 需要 .env 配置 AI_API_KEY（skill/agent 模式请改用 jobs→detail→greet 流程，"
                  "由上层模型完成评估，无需密钥）")
    from main import run as run_auto
    run_auto(argparse.Namespace(
        dry_run=args.dry_run, probe=None, max_jobs=args.max_jobs,
        max_greets=args.max_greets))
    out({"mode": "autonomous_run_finished"})


# ---------------- 入口 ----------------

def main():
    global _OUT_FILE
    argv = sys.argv[1:]
    if argv and argv[0].startswith("-"):
        argv = ["run"] + argv          # 兼容旧用法: python cli.py --dry-run

    p = argparse.ArgumentParser(description="offer-copilot CLI（agent 友好，UTF-8 JSON 输出）")
    p.add_argument("--out", help="JSON 同时写入该文件（UTF-8）")
    p.add_argument("--db", help="指定数据库路径（默认 data/boss.db）")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("doctor", help="环境自检：登录/期望标签/关键选择器逐项体检")
    sub.add_parser("status", help="登录态/期望职位/配额/配置/目标一致性")
    sub.add_parser("jobs", help="当前期望页职位列表（含硬过滤与去重标注）")
    d = sub.add_parser("detail", help="打开职位详情抓JD（含30-40s节奏）")
    d.add_argument("job_id")
    d.add_argument("--title", default="")
    g = sub.add_parser("greet", help="发送打招呼（护栏：节奏/日上限/去重/长度）")
    g.add_argument("job_id")
    g.add_argument("--msg", default="")
    g.add_argument("--msg-file", dest="msg_file", default="",
                   help="从文件读取话术（规避 argv 编码/引号问题，UTF-8）")
    g.add_argument("--title", default="")
    i = sub.add_parser("inbox", help="读取未读会话与消息（只读）")
    i.add_argument("--max", type=int, default=5)
    i.add_argument("--all", action="store_true", help="全量会话存量视图（含最后消息，不打开会话）")
    r = sub.add_parser("reply", help="回复指定会话")
    r.add_argument("name")
    r.add_argument("--msg", default="")
    r.add_argument("--msg-file", dest="msg_file", default="")
    r.add_argument("--brand", default="")
    s = sub.add_parser("resume", help="向指定会话发送在线简历")
    s.add_argument("name")
    s.add_argument("--brand", default="")
    a = sub.add_parser("archive", help="查询职位沉淀档案")
    a.add_argument("--top", type=int, default=20)
    a.add_argument("--status", default="")
    a.add_argument("--query", default="")
    sub.add_parser("setup", help="首次配置：扫码登录+读取期望职位")
    sub.add_parser("star", help="给项目仓库点星（经用户同意后调用）")
    r2 = sub.add_parser("run", help="自主模式（需 .env 的 AI key）")
    r2.add_argument("--dry-run", action="store_true")
    r2.add_argument("--max-jobs", type=int, default=0)
    r2.add_argument("--max-greets", type=int, default=0)

    # --out/--db 是全局选项，也可放在子命令后
    args, extra = p.parse_known_args(argv)
    for item in extra:
        if item.startswith("--out="):
            _OUT_FILE = item.split("=", 1)[1]
        if item.startswith("--db="):
            args.db = item.split("=", 1)[1]
    if not _OUT_FILE:
        _OUT_FILE = getattr(args, "out", None)
    if _OUT_FILE:
        _OUT_FILE = str(Path(_OUT_FILE).absolute())

    {"doctor": cmd_doctor, "status": cmd_status, "jobs": cmd_jobs,
     "detail": cmd_detail, "greet": cmd_greet, "inbox": cmd_inbox,
     "reply": cmd_reply, "resume": cmd_resume, "archive": cmd_archive,
     "setup": cmd_setup, "star": cmd_star, "run": cmd_run}[args.cmd](args)


if __name__ == "__main__":
    main()
