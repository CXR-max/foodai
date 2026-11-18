# UI 截图核对脚本（开发辅助工具，全部放在 D 盘）
# 用法：先启动前后端，然后执行
#   D:\foodai\backend\.venv\Scripts\python.exe tools\ui_shot.py
# 依赖：playwright（调用本机 Edge，不会下载浏览器）
import os
import sys

from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, ".tmp_shots")
BASE = os.environ.get("FOODAI_WEB", "http://localhost:5173")
USER = os.environ.get("FOODAI_USER", "demo_xiaoming")
PWD = os.environ.get("FOODAI_PWD", "demo123456")


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    with sync_playwright() as p:
        b = p.chromium.launch(channel="msedge", headless=True)
        pg = b.new_page(viewport={"width": 1440, "height": 1600})
        pg.goto(f"{BASE}/login", wait_until="networkidle")
        pg.fill('input[placeholder="用户名"]', USER)
        pg.fill('input[placeholder="密码"]', PWD)
        pg.click('button:has-text("登 录")')
        pg.wait_for_url("**/dashboard", timeout=20000)
        pg.wait_for_timeout(2500)
        pg.screenshot(path=os.path.join(OUT, "1_dashboard_7.png"))

        pg.click('label:has-text("近 30 天")')
        pg.wait_for_timeout(1800)
        pg.screenshot(path=os.path.join(OUT, "2_dashboard_30.png"))

        pg.click('li:has-text("数据追踪")')
        pg.wait_for_timeout(2500)
        pg.screenshot(path=os.path.join(OUT, "3_stats_week.png"))

        pg.click('label:has-text("饼图")')
        pg.wait_for_timeout(1500)
        pg.screenshot(path=os.path.join(OUT, "4_stats_pie.png"))

        pg.click('label:has-text("折线图")')
        pg.wait_for_timeout(1200)
        pg.click('label:has-text("近 30 天")')
        pg.wait_for_timeout(2000)
        pg.screenshot(path=os.path.join(OUT, "5_stats_month.png"))

        pg.click('li:has-text("健康打卡")')
        pg.wait_for_timeout(2000)
        pg.screenshot(path=os.path.join(OUT, "6_checkin.png"))
        b.close()
    print("done:", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
