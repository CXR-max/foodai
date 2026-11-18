"""手机视口截图核对：390x844（iPhone 12 Pro）跑一遍移动端五大模块。

用途：改造后快速确认手机布局是否正常（tabBar 高亮、卡片列表、弹窗、横向溢出）。
顺带收集 JS 报错——模板里用了未定义变量是这类改造最常见的坑。

用法（需先启动前后端，默认前端 5173、后端 8000）：
    backend\\.venv\\Scripts\\python.exe scripts\\mobile_shots.py
可用环境变量覆盖：
    FOODAI_WEB / FOODAI_USER / FOODAI_PWD
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

# 页面清单：(文件名, 路由, 底部 tabBar 上应高亮的标题)
PAGES = [
    ("01_plan", "/m/plan", "计划"),
    ("02_schedule", "/m/schedule", "日程"),
    ("03_sport", "/m/sport", "运动"),
    ("04_community", "/m/community", "社区"),
    ("05_me", "/m/me", "我的"),
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

        pg.on("pageerror", lambda e: errors.append(f"JS 错误: {e}"))
        pg.on("console", lambda m: errors.append(f"控制台 {m.type}: {m.text}") if m.type == "error" else None)

        # 登录：移动端登录后应落到 /m/schedule
        pg.goto(f"{BASE}/login", wait_until="networkidle")
        pg.fill('input[placeholder="用户名"]', USER)
        pg.fill('input[placeholder="密码"]', PWD)
        pg.click('button:has-text("登 录")')
        try:
            pg.wait_for_url("**/m/schedule", timeout=20000)
            print("[OK] 登录后落到移动端首页 /m/schedule")
        except Exception:
            pg.screenshot(path=str(OUT / "00_login_failed.png"))
            print("[X] 未落到 /m/schedule，已截图 00_login_failed.png")
            print("   当前 URL:", pg.url)
            browser.close()
            return 1
        pg.wait_for_timeout(1500)

        for name, path, tab in PAGES:
            pg.goto(f"{BASE}{path}", wait_until="networkidle")
            pg.wait_for_timeout(1600)
            shot = OUT / f"{name}.png"
            pg.screenshot(path=str(shot), full_page=True)
            print(f"[OK] {path:14s} -> {shot.name}")

            if pg.locator("nav.tabbar").count() == 0:
                errors.append(f"{path}: 底部 tabBar 未渲染")
            else:
                active = pg.locator("nav.tabbar .tab.active .label").inner_text().strip()
                if active != tab:
                    errors.append(f"{path}: tabBar 高亮是「{active}」，应为「{tab}」")

            overflow = pg.evaluate(
                "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
            )
            if overflow and overflow > 2:
                errors.append(f"{path}: 横向溢出 {overflow}px")

        # 计划详情：点第一张计划卡片 → 展开第一天 → 截图
        pg.goto(f"{BASE}/m/plan", wait_until="networkidle")
        pg.wait_for_timeout(1200)
        if pg.locator(".plan-card").count():
            pg.locator(".plan-card").first.click()
            pg.wait_for_timeout(1500)
            pg.screenshot(path=str(OUT / "06_plan_detail.png"), full_page=True)
            print("[OK] /m/plan 详情页 -> 06_plan_detail.png")
            if pg.locator(".day-head").count():
                pg.locator(".day-head").first.click()
                pg.wait_for_timeout(600)
                pg.screenshot(path=str(OUT / "07_plan_detail_day.png"), full_page=True)
                print("[OK] 计划详情展开第一天 -> 07_plan_detail_day.png")
        else:
            errors.append("/m/plan: 没有计划卡片（明细页未验证）")

        # 日程页展开打卡日历
        pg.goto(f"{BASE}/m/schedule", wait_until="networkidle")
        pg.wait_for_timeout(1200)
        if pg.locator(".cal-bar").count():
            pg.locator(".cal-bar").click()
            pg.wait_for_timeout(800)
            pg.screenshot(path=str(OUT / "08_schedule_calendar.png"), full_page=True)
            print("[OK] 日程打卡日历 -> 08_schedule_calendar.png")

        # 桌面宽度访问移动端路由应被重定向回仪表盘
        desk = browser.new_context(viewport={"width": 1440, "height": 900})
        dp = desk.new_page()
        dp.goto(f"{BASE}/login", wait_until="networkidle")
        dp.fill('input[placeholder="用户名"]', USER)
        dp.fill('input[placeholder="密码"]', PWD)
        dp.click('button:has-text("登 录")')
        dp.wait_for_timeout(2000)
        dp.goto(f"{BASE}/m/schedule", wait_until="networkidle")
        dp.wait_for_timeout(800)
        if "/dashboard" not in dp.url:
            errors.append(f"桌面访问 /m/schedule 未重定向到仪表盘（当前 {dp.url}）")
        else:
            print("[OK] 桌面访问移动端路由已重定向到 /dashboard")
        desk.close()

        browser.close()

    print("\n=== 检查结果 ===")
    if errors:
        for e in dict.fromkeys(errors):
            print("  [!]", e)
    else:
        print("  没有 JS 报错、没有横向溢出、tabBar 高亮正确、桌面重定向正常")
    print(f"截图目录：{OUT}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
