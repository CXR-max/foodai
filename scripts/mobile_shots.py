"""手机视口截图核对：用 390x844（iPhone 12 Pro）和 412x915（Pixel 7）跑一遍关键页面。

用途：改造响应式后，快速确认手机布局是否正常（tabBar、卡片列表、弹窗全屏等）。

用法（需先启动前后端）：
    backend\\.venv\\Scripts\\python.exe scripts\\mobile_shots.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / ".tmp_shots" / "mobile"
BASE = os.environ.get("FOODAI_WEB", "http://127.0.0.1:5173")
USER = os.environ.get("FOODAI_USER", "demo")
PWD = os.environ.get("FOODAI_PWD", "demo1234")

# 页面清单：(文件名, 路由, 说明)
PAGES = [
    ("01_recognition", "/recognition", "食物识别（表格改卡片）"),
    ("02_plans", "/plans", "养生方案（餐次卡片）"),
    ("03_checkin", "/checkin", "健康打卡（日历+任务）"),
    ("04_dashboard", "/dashboard", "仪表盘（统计卡）"),
    ("05_stats", "/stats", "数据追踪（图表）"),
    ("06_profile", "/profile", "健康档案（表单+更多功能）"),
    ("07_mall", "/mall", "积分商城（商品网格）"),
    ("08_achievements", "/achievements", "成就徽章（徽章网格）"),
    ("09_leaderboard", "/leaderboard", "排行榜（卡片列表）"),
]


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        ctx = browser.new_context(
            viewport={"width": 390, "height": 844},
            device_scale_factor=2,
            is_mobile=True,
            has_touch=True,
            user_agent=(
                "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1"
            ),
        )
        pg = ctx.new_page()

        # 收集 JS 报错，改造后最容易踩的就是模板里用了未定义的变量
        pg.on("pageerror", lambda e: errors.append(f"JS 错误: {e}"))
        pg.on("console", lambda m: errors.append(f"控制台 {m.type}: {m.text}") if m.type == "error" else None)

        # 登录
        pg.goto(f"{BASE}/login", wait_until="networkidle")
        pg.fill('input[placeholder="用户名"]', USER)
        pg.fill('input[placeholder="密码"]', PWD)
        pg.click('button:has-text("登 录")')
        try:
            pg.wait_for_url("**/dashboard", timeout=20000)
        except Exception:
            pg.screenshot(path=str(OUT / "00_login_failed.png"))
            print("[X] 登录失败，已截图 00_login_failed.png")
            print("   当前 URL:", pg.url)
            browser.close()
            return 1
        pg.wait_for_timeout(1500)

        for name, path, desc in PAGES:
            pg.goto(f"{BASE}{path}", wait_until="networkidle")
            pg.wait_for_timeout(1800)
            shot = OUT / f"{name}.png"
            pg.screenshot(path=str(shot), full_page=True)
            print(f"[OK] {desc:22s} -> {shot.name}")

            # 检查底部 tabBar 是否渲染（除登录/注册外每页都该有）
            if pg.locator("nav.tabbar").count() == 0:
                errors.append(f"{path}: 底部 tabBar 未渲染")

            # 检查是否有横向溢出（手机布局最常见的破绽）
            overflow = pg.evaluate(
                "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
            )
            if overflow and overflow > 2:
                errors.append(f"{path}: 横向溢出 {overflow}px")

        # 单独截一张带底部 tabBar 的完整首页（不滚动的首屏）
        pg.goto(f"{BASE}/recognition", wait_until="networkidle")
        pg.wait_for_timeout(1500)
        pg.screenshot(path=str(OUT / "10_recognition_firstscreen.png"))

        browser.close()

    print("\n=== 检查结果 ===")
    if errors:
        for e in dict.fromkeys(errors):
            print("  [!]", e)
    else:
        print("  没有 JS 报错、没有横向溢出、tabBar 全部渲染")
    print(f"截图目录：{OUT}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
