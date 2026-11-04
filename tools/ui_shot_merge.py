# 合并后 UI 核对脚本（开发辅助，全部在 D 盘）
# 用法：D:\foodai\backend\.venv\Scripts\python.exe tools\ui_shot_merge.py
# 作用：逐页截图 + 收集控制台报错，确认远端新页面与本地个性化功能在合并后都正常
import os
import sys

from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, ".tmp_shots")
BASE = os.environ.get("FOODAI_WEB", "http://localhost:5173")
USER = os.environ.get("FOODAI_USER", "demo_xiaoming")
PWD = os.environ.get("FOODAI_PWD", "demo123456")

PAGES = [
    ("m1_dashboard", "仪表盘", "li:has-text(\"仪表盘\")"),
    ("m2_checkin", "健康打卡", "li:has-text(\"健康打卡\")"),
    ("m3_stats", "数据追踪", "li:has-text(\"数据追踪\")"),
    ("m4_achievements", "成就", "li:has-text(\"成就\")"),
    ("m5_plans", "方案", "li:has-text(\"健康方案\")"),
    ("m6_mall", "商城", "li:has-text(\"积分商城\")"),
    ("m7_leaderboard", "排行榜", "li:has-text(\"排行榜\")"),
]


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    errors: list[str] = []
    with sync_playwright() as p:
        b = p.chromium.launch(channel="msedge", headless=True)
        pg = b.new_page(viewport={"width": 1440, "height": 1500})
        pg.on("console", lambda m: errors.append(f"[{m.type}] {m.text}") if m.type == "error" else None)
        pg.on("pageerror", lambda e: errors.append(f"[pageerror] {e}"))

        pg.goto(f"{BASE}/login", wait_until="networkidle")
        pg.fill('input[placeholder="用户名"]', USER)
        pg.fill('input[placeholder="密码"]', PWD)
        pg.click('button:has-text("登 录")')
        pg.wait_for_url("**/dashboard", timeout=20000)
        pg.wait_for_timeout(2500)

        for name, label, sel in PAGES:
            try:
                pg.click(sel, timeout=6000)
                pg.wait_for_timeout(2200)
                pg.screenshot(path=os.path.join(OUT, f"{name}.png"))
                print(f"ok   {label}")
            except Exception as e:  # 菜单项不存在时跳过，不中断
                print(f"skip {label}: {str(e).splitlines()[0][:80]}")
        b.close()

    print("done:", OUT)
    if errors:
        print("=== 控制台报错 ===")
        for e in errors[:12]:
            print(" ", e[:180])
    else:
        print("控制台无报错")
    return 0


if __name__ == "__main__":
    sys.exit(main())
