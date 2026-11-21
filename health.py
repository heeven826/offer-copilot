"""REQ-10 账号健康：指标计算与告警判定（纯函数，可单测）。

三块内容（P0 切片，可视化后置）：
1. 今日操作审计汇总（一屏回看）
2. 两条预警线：发送失败率、风控事件频率
3. 质量退化哨兵：详情抓取成功率、打分分布周漂移、话术截断率
   ——专抓"整体劣化但没掉零"的静默退化（空转告警的盲区）
"""
from datetime import date, timedelta


def rate(part: int, total: int) -> float:
    return round(part / total, 3) if total else 0.0


def fail_rate(events: list, window: int = 20) -> dict:
    """最近 window 次 greet 的失败率。events=[{'outcome':...}] 按时间正序。"""
    recent = events[-window:]
    fails = sum(1 for e in recent if e["outcome"] != "ok")
    return {"value": rate(fails, len(recent)), "samples": len(recent), "window": window}


def jd_success_rate(events: list, window: int = 30) -> dict:
    """详情抓取成功率：detail 事件中 jd 非空的比例（滚动窗口）。"""
    recent = events[-window:]
    ok = 0
    for e in recent:
        try:
            import json
            d = json.loads(e["detail"] or "{}")
            if d.get("jd_len", 0) > 0:
                ok += 1
        except Exception:
            pass
    return {"value": rate(ok, len(recent)), "samples": len(recent), "window": window}


def truncate_rate(events: list, window: int = 30) -> dict:
    """话术截断率：greet 事件中 truncated=true 的比例（长度约束失守的信号）。"""
    recent = events[-window:]
    t = 0
    for e in recent:
        try:
            import json
            d = json.loads(e["detail"] or "{}")
            if d.get("truncated"):
                t += 1
        except Exception:
            pass
    return {"value": rate(t, len(recent)), "samples": len(recent), "window": window}


def score_drift(score_rows: list) -> dict:
    """打分分布周漂移：本周 vs 上周平均分（applications 表为原料）。

    rows=[{'score':int,'created_at':'YYYY-MM-DD...'}]
    样本不足只报基线状态不判漂移（P0 阶段就是攒基线）。
    """
    today = date.today()
    wk_start = today - timedelta(days=today.weekday())      # 本周一
    last_start = wk_start - timedelta(days=7)

    def _avg(rows):
        scores = [r["score"] for r in rows if r.get("score") is not None]
        return (round(sum(scores) / len(scores), 2), len(scores)) if scores else (None, 0)

    this_rows = [r for r in score_rows if r["created_at"][:10] >= str(wk_start)]
    last_rows = [r for r in score_rows
                 if str(last_start) <= r["created_at"][:10] < str(wk_start)]
    this_avg, this_n = _avg(this_rows)
    last_avg, last_n = _avg(last_rows)
    judgeable = bool(last_n >= 10 and this_n >= 10)
    # 样本不足不判漂移（反噪声原则：样本不足的差值是噪声不是信号）
    drift = (round(this_avg - last_avg, 2)
             if judgeable and this_avg is not None and last_avg is not None else None)
    return {"this_week": {"avg": this_avg, "samples": this_n},
            "last_week": {"avg": last_avg, "samples": last_n},
            "drift": drift,
            "judgeable": judgeable}


def summarize(db, cfg: dict) -> dict:
    """汇总健康状态：今日审计 + 预警线 + 哨兵。返回 healthy/warning 与告警列表。"""
    h = (cfg.get("health") or {})
    today = date.today().isoformat()
    warnings = []

    # ---- 今日操作汇总（一屏回看） ----
    rows = db.audit_rows(since_like=today)
    by_action = {}
    for r in rows:
        key = (r["action"], r["outcome"])
        by_action[key] = by_action.get(key, 0) + 1
    today_summary = {}
    for (action, outcome), n in sorted(by_action.items()):
        today_summary.setdefault(action, {})[outcome] = n

    # ---- 预警线 1：发送失败率（滚动 20 次） ----
    greets = [dict(e) for e in db.audit_rows(action="greet")][::-1]   # 正序
    fr = fail_rate(greets)
    line = h.get("send_fail_rate", 0.35)
    if fr["samples"] >= 5 and fr["value"] > line:
        warnings.append({"code": "SEND_FAIL_HIGH",
                         "hint": f"近{fr['samples']}次打招呼失败率 {fr['value']:.0%} > 预警线{line:.0%}，"
                                 "检查是否职位批量关闭/风控收紧（跑 doctor）"})

    # ---- 预警线 2：风控事件频率（24h） ----
    risk_rows = db.audit_rows(action="risk")
    from datetime import datetime, timedelta
    day_ago = (datetime.now() - timedelta(hours=24)).isoformat(timespec="seconds")
    risk_24h = sum(1 for r in risk_rows if r["ts"] >= day_ago)
    risk_line = h.get("risk_events_24h", 2)
    if risk_24h >= risk_line:
        warnings.append({"code": "RISK_FREQUENT",
                         "hint": f"24h 内风控事件 {risk_24h} 次 ≥ 预警线 {risk_line}——建议今日降速或暂停"})

    # ---- 哨兵 1：详情抓取成功率 ----
    details = [dict(e) for e in db.audit_rows(action="detail")][::-1]
    jr = jd_success_rate(details)
    jd_line = h.get("detail_jd_rate", 0.7)
    if jr["samples"] >= 10 and jr["value"] < jd_line:
        warnings.append({"code": "JD_FETCH_DEGRADED",
                         "hint": f"详情JD抓取成功率 {jr['value']:.0%} < {jd_line:.0%}"
                                 f"（{jr['samples']}次窗口）——疑似站点改版，跑 doctor 校准"})

    # ---- 哨兵 2：话术截断率 ----
    tr = truncate_rate(greets)
    tr_line = h.get("msg_truncate_rate", 0.2)
    if tr["samples"] >= 10 and tr["value"] > tr_line:
        warnings.append({"code": "MSG_TRUNCATE_HIGH",
                         "hint": f"话术超长截断率 {tr['value']:.0%} > {tr_line:.0%}——提示词长度约束在失效"})

    # ---- 哨兵 3：打分周漂移（攒基线为主） ----
    score_rows = [dict(r) for r in db.conn.execute(
        "SELECT score, created_at FROM applications WHERE score IS NOT NULL")]
    sd = score_drift(score_rows)
    if sd["judgeable"] and abs(sd["drift"]) >= 1.5:
        warnings.append({"code": "SCORE_DRIFT",
                         "hint": f"打分均值周漂移 {sd['drift']:+.1f}"
                                 f"（上周{sd['last_week']['avg']}→本周{sd['this_week']['avg']}）——"
                                 "池子质量或解析质量在静默变化"})

    return {
        "status": "warning" if warnings else "healthy",
        "today": today_summary,
        "today_events": len(rows),
        "lines": {
            "send_fail_rate": {"value": fr["value"], "samples": fr["samples"], "line": line},
            "risk_events_24h": {"value": risk_24h, "line": risk_line},
            "detail_jd_rate": {"value": jr["value"], "samples": jr["samples"], "line": jd_line},
            "msg_truncate_rate": {"value": tr["value"], "samples": tr["samples"], "line": tr_line},
            "score_drift": sd,
        },
        "warnings": warnings,
    }
