"""探测「期望职位推荐页」—— 打开 https://www.zhipin.com/web/geek/jobs

把页面 HTML、整页截图、候选元素报告存到 samples/，供字段提取分析。

用法：.venv/Scripts/python.exe probe_expect.py
"""
import sys
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from boss_browser.browser import BossBrowser
from boss_browser.risk import RiskControlError

EXPECT_URL = "https://www.zhipin.com/web/geek/jobs"


def dump_page(tab, name: str):
    samples = ROOT / "samples"
    samples.mkdir(exist_ok=True)
    html_path = samples / f"{name}.html"
    html_path.write_text(tab.html or "", encoding="utf-8")
    print(f"[ok] HTML   -> samples/{html_path.name} ({html_path.stat().st_size:,} bytes)")
    try:
        shot = samples / f"{name}.png"
        tab.get_screenshot(str(shot), full_page=True)
        print(f"[ok] 截图   -> samples/{shot.name}")
    except Exception as e:
        print(f"[skip] 截图失败: {e}")


def element_report(tab) -> str:
    """列出关键候选元素，快速定位结构。"""
    lines = []
    for sel in ("css:.job-card-wrapper",
                "css:[class*=expect]",
                "css:[class*=tab]",
                "css:[class*=filter]"):
        try:
            els = tab.eles(sel)
            lines.append(f"\n=== {sel} -> {len(els)} 个 ===")
            for e in els[:12]:
                cls = e.attr("class")
                txt = (e.text or "").replace("\n", " ")[:60]
                lines.append(f"  <{e.tag}> class={cls} | text={txt!r}")
        except Exception as ex:
            lines.append(f"\n=== {sel} 出错: {ex}")
    return "\n".join(lines)


def main():
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    b = BossBrowser(cfg, ROOT)
    tab = b.connect()

    print("等待登录（如浏览器显示登录页，请扫码；已登录会自动跳过）…")
    if not b.ensure_login(timeout=480):
        print("!! 8 分钟内未完成登录，退出。重新运行本脚本再试")
        return

    print("登录 OK，打开期望职位页…")
    try:
        tab.get(EXPECT_URL)
    except RiskControlError as e:
        print(f"!! 触发风控: {e} —— 请在浏览器手动完成后重跑本脚本")
        return
    time.sleep(4)   # 等前端渲染

    dump_page(tab, "expect_page")

    report = element_report(tab)
    (ROOT / "samples" / "expect_report.txt").write_text(report, encoding="utf-8")
    print(f"[ok] 报告   -> samples/expect_report.txt")
    print(report)


if __name__ == "__main__":
    main()
