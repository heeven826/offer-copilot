"""SQLite 存储：职位档案沉淀、投递记录、每日配额统计。零依赖（标准库）。

jobs 表 = 职位档案库（所有见过的职位全量沉淀，status 区分处理进度）：
  seen    仅入库（未处理/演练过，正式运行时仍会处理）
  skipped 已被 AI/黑名单明确跳过（不再处理）
  greeted 已打招呼（不再处理）
  failed  打招呼失败（下次可重试）
"""
import json
import sqlite3
from datetime import date
from pathlib import Path

from loguru import logger

from .models import Application, Job

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id      TEXT PRIMARY KEY,
    title       TEXT,
    salary      TEXT,
    company     TEXT,
    link        TEXT,
    status      TEXT DEFAULT 'seen',
    raw_json    TEXT,
    first_seen  TEXT DEFAULT (datetime('now', 'localtime'))
);
CREATE TABLE IF NOT EXISTS applications (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      TEXT,
    title       TEXT,
    company     TEXT,
    status      TEXT,
    score       INTEGER,
    reason      TEXT,
    greeting    TEXT,
    created_at  TEXT
);
CREATE TABLE IF NOT EXISTS replies (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    boss_name   TEXT,
    brand       TEXT,
    action      TEXT,          -- reply / resume / skip
    text        TEXT,
    created_at  TEXT
);
CREATE TABLE IF NOT EXISTS audit (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT,
    action      TEXT,          -- detail / greet / reply / resume / risk / truncate
    target      TEXT,          -- job_id 或会话名
    outcome     TEXT,          -- ok / fail / warn
    detail      TEXT           -- JSON: {duration, jd_len, stage, msg_len, ...}
);
CREATE TABLE IF NOT EXISTS state (
    key         TEXT PRIMARY KEY,
    value       TEXT
);
CREATE INDEX IF NOT EXISTS idx_app_job ON applications(job_id);
CREATE INDEX IF NOT EXISTS idx_app_created ON applications(created_at);
"""

# 视为"已处理完毕"的状态（下次运行跳过）
DONE_STATUSES = ("skipped", "greeted")


class Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._migrate()

    def _migrate(self):
        """老库迁移：jobs 表补 status 列，并按 applications 历史回填。"""
        cols = [r["name"] for r in self.conn.execute("PRAGMA table_info(jobs)")]
        if "status" not in cols:
            self.conn.execute(
                "ALTER TABLE jobs ADD COLUMN status TEXT DEFAULT 'seen'")
            self.conn.execute(
                "UPDATE jobs SET status='greeted' WHERE job_id IN "
                "(SELECT job_id FROM applications WHERE status IN ('greeted','resume_sent'))")
            self.conn.execute(
                "UPDATE jobs SET status='skipped' WHERE job_id IN "
                "(SELECT job_id FROM applications WHERE status='skipped')")
            self.conn.commit()
            logger.info("数据库迁移完成：jobs.status 已回填")

    # ---------- 职位档案 ----------

    def save_job(self, job: Job):
        """全量沉淀：所有见过的职位都入库（不覆盖已有状态）。"""
        self.conn.execute(
            "INSERT OR IGNORE INTO jobs (job_id, title, salary, company, link, status, raw_json) "
            "VALUES (?,?,?,?,?,'seen',?)",
            (job.job_id, job.title, job.salary, job.company, job.link,
             json.dumps(job.to_dict(), ensure_ascii=False)),
        )
        self.conn.commit()

    def is_done(self, job_id: str) -> bool:
        """该职位是否已有终态决定（跳过/已打招呼）—— 用于查重。

        jobs 行可能从未落库（agent 流程早期版本不写 jobs 表），
        回落查 applications 的投递记录，避免历史投递被重复打招呼。
        """
        if not job_id:
            return False
        cur = self.conn.execute("SELECT status FROM jobs WHERE job_id=?", (job_id,))
        row = cur.fetchone()
        if row and row["status"] in DONE_STATUSES:
            return True
        cur = self.conn.execute(
            "SELECT 1 FROM applications WHERE job_id=? "
            "AND status IN ('greeted', 'resume_sent') LIMIT 1",
            (job_id,),
        )
        return cur.fetchone() is not None

    def get_job(self, job_id: str) -> dict:
        """读职位档案（jobs 行 + raw_json 字段合并）；不存在返回 {}。

        用途：greet 定位会话需要 HR 姓名（R25-4：由落库数据带出，不靠调用方手传）。
        """
        row = self.conn.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        if not row:
            return {}
        d = dict(row)
        try:
            d.update(json.loads(d.get("raw_json") or "{}"))
        except Exception:
            pass
        return d

    def mark_job(self, job_id: str, status: str):
        """写入处理终态；jobs 行可能不存在（agent 流程），必须 upsert。"""
        self.conn.execute(
            "INSERT INTO jobs (job_id, status) VALUES (?,?) "
            "ON CONFLICT(job_id) DO UPDATE SET status=excluded.status",
            (job_id, status),
        )
        self.conn.commit()

    def update_job_desc(self, job: Job):
        """职位描述抓取后回写档案（含 job_desc 的完整数据）；行可能不存在，upsert。"""
        self.conn.execute(
            "INSERT INTO jobs (job_id, title, salary, company, link, raw_json) "
            "VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(job_id) DO UPDATE SET raw_json=excluded.raw_json",
            (job.job_id, job.title, job.salary, job.company, job.link,
             json.dumps(job.to_dict(), ensure_ascii=False)))
        self.conn.commit()

    # ---------- 投递记录 ----------

    def record(self, app: Application):
        self.conn.execute(
            "INSERT INTO applications "
            "(job_id, title, company, status, score, reason, greeting, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (app.job_id, app.title, app.company, app.status, app.score,
             app.reason, app.greeting, app.created_at),
        )
        self.conn.commit()

    def greeted_today(self) -> int:
        today = date.today().isoformat()
        cur = self.conn.execute(
            "SELECT COUNT(*) c FROM applications "
            "WHERE status IN ('greeted','resume_sent') AND created_at LIKE ?",
            (today + "%",),
        )
        return cur.fetchone()["c"]

    def stats(self) -> dict:
        cur = self.conn.execute(
            "SELECT status, COUNT(*) c FROM applications GROUP BY status")
        return {r["status"]: r["c"] for r in cur.fetchall()}

    # ---------- 收件箱回复记录 ----------

    def record_reply(self, boss_name: str, brand: str, action: str, text: str):
        from datetime import datetime
        self.conn.execute(
            "INSERT INTO replies (boss_name, brand, action, text, created_at) VALUES (?,?,?,?,?)",
            (boss_name, brand, action, text,
             datetime.now().isoformat(timespec="seconds")),
        )
        self.conn.commit()

    # ---------- 操作审计（REQ-10：agent 代操作的一屏回看与健康指标原料） ----------

    def audit(self, action: str, target: str = "", outcome: str = "ok", **detail):
        """记录一条审计事件（greet/detail/risk 等，Communicator/inbox 自动调用）。"""
        from datetime import datetime
        self.conn.execute(
            "INSERT INTO audit (ts, action, target, outcome, detail) VALUES (?,?,?,?,?)",
            (datetime.now().isoformat(timespec="seconds"), action, target, outcome,
             json.dumps(detail, ensure_ascii=False)))
        self.conn.commit()

    def audit_rows(self, action: str = "", since_like: str = "") -> list:
        """查询审计事件（health/诊断用）。"""
        sql, params = "SELECT * FROM audit", []
        cond = []
        if action:
            cond.append("action=?")
            params.append(action)
        if since_like:
            cond.append("ts LIKE ?")
            params.append(since_like + "%")
        if cond:
            sql += " WHERE " + " AND ".join(cond)
        sql += " ORDER BY id DESC"
        return self.conn.execute(sql, params).fetchall()

    # ---------- 跨进程护栏状态（CLI 每次调用是新进程，节奏状态须持久化） ----------

    def get_state(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def set_state(self, key: str, value: str):
        self.conn.execute(
            "INSERT OR REPLACE INTO state (key, value) VALUES (?,?)", (key, value))
        self.conn.commit()
