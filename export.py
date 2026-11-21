"""导出职位沉淀数据 —— 供简历优化、面试准备使用。

用法:
  .venv/Scripts/python.exe export.py            # 导出全部 → exports/jobs_全量.csv
  .venv/Scripts/python.exe export.py greeted    # 只导出已打招呼的
  .venv/Scripts/python.exe export.py top 30     # 按 AI 匹配分导出前 30

输出 CSV（Excel 可直接打开，UTF-8-BOM），另附一份 Markdown 摘要（高分职位速览）。
"""
import csv
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from storage.db import Database   # 走 Database 以触发老库迁移

EXPORTS = ROOT / "exports"

COLS = [("title", "职位名"), ("salary", "薪资"), ("company", "公司"),
        ("company_info", "公司信息"), ("hr_name", "招聘者"), ("hr_title", "招聘者职位"),
        ("location", "地点"), ("experience", "经验要求"), ("education", "学历"),
        ("tags", "技能标签"), ("welfare", "福利"), ("link", "链接"),
        ("score", "AI匹配分"), ("reason", "AI理由"), ("status", "处理状态"),
        ("first_seen", "首次发现")]


def main():
    db = Database(ROOT / "data" / "boss.db")
    conn = db.conn

    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else None

    where, order = "", "ORDER BY score IS NULL, score DESC"
    if mode == "greeted":
        where = "WHERE j.status IN ('greeted')"
    sql = f"""
        SELECT j.*, (SELECT MAX(score) FROM applications a WHERE a.job_id = j.job_id) AS score,
               (SELECT reason FROM applications a WHERE a.job_id = j.job_id
                ORDER BY a.id DESC LIMIT 1) AS reason
        FROM jobs j {where} {order}
    """
    rows = conn.execute(sql).fetchall()
    if limit:
        rows = rows[:limit]

    if not rows:
        print("没有数据可导出")
        return

    EXPORTS.mkdir(exist_ok=True)
    tag = f"{mode}_{date.today().isoformat()}"
    csv_path = EXPORTS / f"jobs_{tag}.csv"
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as fp:
        w = csv.writer(fp)
        w.writerow([c[1] for c in COLS])
        for r in rows:
            raw = json.loads(r["raw_json"] or "{}")
            w.writerow([
                (", ".join(raw.get(k)) if isinstance(raw.get(k), list) else raw.get(k, ""))
                if k in ("tags", "welfare") else
                (r[k] if k in ("score", "reason", "status", "first_seen") else raw.get(k, ""))
                for k, _ in COLS])

    md_path = EXPORTS / f"jobs_{tag}.md"
    with open(md_path, "w", encoding="utf-8") as fp:
        fp.write(f"# 职位沉淀速览（{mode}，{len(rows)} 条，{date.today()}）\n\n")
        fp.write("| 职位 | 薪资 | 公司 | 分数 | 状态 | 一句话理由 |\n|---|---|---|---|---|---|\n")
        for r in rows:
            raw = json.loads(r["raw_json"] or "{}")
            fp.write(f"| {raw.get('title','')} | {raw.get('salary','')} | "
                     f"{raw.get('company','')} | {r['score'] or '-'} | {r['status']} "
                     f"| {(r['reason'] or '')[:40]} |\n")

    print(f"导出完成: {csv_path.name}（{len(rows)} 条）")
    print(f"速览摘要: {md_path.name}")
    conn.close()


if __name__ == "__main__":
    main()
