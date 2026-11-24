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


def score_drift(score_rows: list, today=None) -> dict:
    """打分分布周漂移：本周 vs 上周平均分（按 pass/reject 分组，R17-2）。

    - R14-1：score<=0 视为"未评分"，排除出均值（unscored 单列）
    - R17-2：pass（greeted/evaluated_pass）与 reject（evaluated_reject）分组各报均值
      ——混合均值会混淆"质量变化"与"投递策略变化"，分组合并诊断
    - R17-5 语义：greeted 带分视作 pass 组（投了=通过）
    """
    today = today or date.today()     # R23-4：可注入日期，消除测试的星期依赖
    wk_start = today - timedelta(days=today.weekday())
    last_start = wk_start - timedelta(days=7)

    valid = [r for r in score_rows if (r.get("score") or 0) > 0]
    unscored = len(score_rows) - len(valid)
    for r in valid:
        r["group"] = "reject" if r.get("status") == "evaluated_reject" else "pass"

    def _avg(rows):
        scores = [r["score"] for r in rows]
        return (round(sum(scores) / len(scores), 2), len(scores)) if scores else (None, 0)

    def _week(rows, start, end=None):
        if end is None:
            return [r for r in rows if r["created_at"][:10] >= str(start)]
        return [r for r in rows
                if str(start) <= r["created_at"][:10] < str(end)]

    result = {"unscored_excluded": unscored}
    for label, start, end in (("this_week", wk_start, None),
                              ("last_week", last_start, wk_start)):
        wk = _week(valid, start, end)
        p_avg, p_n = _avg([r for r in wk if r["group"] == "pass"])
        r_avg, r_n = _avg([r for r in wk if r["group"] == "reject"])
        result[label] = {"pass_avg": p_avg, "pass_n": p_n,
                         "reject_avg": r_avg, "reject_n": r_n}

    tn = result["this_week"]["pass_n"] + result["this_week"]["reject_n"]
    ln = result["last_week"]["pass_n"] + result["last_week"]["reject_n"]
    judgeable = bool(tn >= 10 and ln >= 10)
    drift = None
    if judgeable:
        def _combined(wk):
            s = [r["score"] for r in wk]
            return round(sum(s) / len(s), 2) if s else None
        this_c = _combined(_week(valid, wk_start))
        last_c = _combined(_week(valid, last_start, wk_start))
        if this_c is not None and last_c is not None:
            drift = round(this_c - last_c, 2)
    insufficient = None
    if not judgeable:
        insufficient = f"本周{tn}/10，上周{ln}/10 有效样本"
    result.update({"drift": drift, "judgeable": judgeable, "insufficient": insufficient})
    return result


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

    # ---- 哨兵 3：打分周漂移（攒基线为主；R14-1：只统计 score>0；R17-2：pass/reject分组） ----
    score_rows = [dict(r) for r in db.conn.execute(
        "SELECT score, status, created_at FROM applications")]
    sd = score_drift(score_rows)
    if sd["judgeable"] and sd["drift"] is not None and abs(sd["drift"]) >= 1.5:
        tw, lw = sd["this_week"], sd["last_week"]
        warnings.append({"code": "SCORE_DRIFT",
                         "hint": f"打分均值周漂移 {sd['drift']:+.1f}"
                                 f"（pass: {lw['pass_avg']}→{tw['pass_avg']} n={tw['pass_n']}; "
                                 f"reject: {lw['reject_avg']}→{tw['reject_avg']} n={tw['reject_n']}）——"
                                 "两组同向=质量真变化；仅混合值动=投递策略变化"})

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
