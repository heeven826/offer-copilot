"""重新登录 + 采样期望职位页（一次性、轻量，避免触发风控）。

流程：打开期望职位页(仅1次) → 元素判断登录态 → 游客态则点「登录」弹出二维码
→ 静默轮询等扫码 → 登录后等待渲染 → 存 HTML/截图/元素报告。
"""
import sys
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from boss_browser.browser import BossBrowser
from boss_browser.risk import check_risk

EXPECT_URL = "https://www.zhipin.com/web/geek/jobs"


def is_guest(tab) -> bool:
    """用页面元素判断：头部存在精确为「登录」的链接 => 游客。"""
    try:
        return bool(tab.eles('xpath://a[normalize-space(text())="登录"]'))
    except Exception:
        return True


def dump(tab, name):
    samples = ROOT / "samples"
    samples.mkdir(exist_ok=True)
    (samples / f"{name}.html").write_text(tab.html or "", encoding="utf-8")
    print(f"[ok] HTML -> samples/{name}.html ({len(tab.html or ''):,} bytes)")
    try:
        tab.get_screenshot(str(samples / f"{name}.png"), full_page=True)
        print(f"[ok] 截图 -> samples/{name}.png")
    except Exception as e:
        print(f"[skip] 截图失败: {e}")


def main():
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    b = BossBrowser(cfg, ROOT)
    tab = b.connect()

    tab.get(EXPECT_URL)          # 只加载这一次
    try:
        check_risk(tab)
    except Exception as e:
        print(f"!! 触发风控({e})，请在浏览器手动完成后重新运行本脚本")
        return
    time.sleep(5)

    if is_guest(tab):
        print("当前游客态 —— 点出登录二维码，请用手机 Boss直聘 App 扫码…")
        try:
            btn = tab.ele('xpath://a[normalize-space(text())="登录"]')
            if btn:
                btn.click()
        except Exception as e:
            print(f"自动点击登录失败({e})，请手动点浏览器右上角「登录」")
        deadline = time.time() + 420
        while time.time() < deadline:
            time.sleep(6)
            if not is_guest(tab):
                break
        else:
            print("!! 7 分钟未完成扫码，退出。重新运行本脚本再试")
            return
        print("扫码成功！")
        time.sleep(3)
        tab.get(EXPECT_URL)      # 登录后重新进入期望页
        time.sleep(5)
    else:
        print("已是登录态")

    # 等职位列表渲染出来
    try:
        tab.wait.ele_displayed("css:[class*=job-card]", timeout=15)
    except Exception:
        print("[warn] 15s 内没等到 job-card 元素，照样采样")

    dump(tab, "expect_logged")

    # 元素报告：3个期望标签 + 职位卡片
    print("\n=== 期望职位标签（按用户配置的3个期望名定位）===")
    for kw in ("全栈工程师", "技术合伙人", "技术总监", "全栈开发", "架构师"):
        els = tab.eles(f'xpath://*[contains(text(),"{kw}")]')
        print(f"「{kw}」命中 {len(els)} 个")
        for e in els[:2]:
            chain, p = [], e
            for _ in range(4):
                try:
                    chain.append(f'{p.tag}.{(p.attr("class") or "")[:50]}')
                    p = p.parent()
                except Exception:
                    break
            print("   ", " > ".join(chain))

    print("\n=== job-card 元素 ===")
    cards = tab.eles("css:[class*=job-card]")
    print(f"共 {len(cards)} 个")
    if cards:
        print("第一个卡片 outerHTML 前600字符:")
        print(cards[0].html[:600])


if __name__ == "__main__":
    main()
