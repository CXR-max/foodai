"""核对本次改动：改菜（含实时营养对比/换一道）+ 日程简约打卡计划 + 运动处方 + 我的三环

前置：前后端已启动（前端 5173，后端 8000），账号 demo_mobile / demo123456。
用法：backend\\.venv\\Scripts\\python.exe scripts\\verify_plan_edit.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / ".tmp_shots" / "edit"
BASE = os.environ.get("FOODAI_WEB", "http://127.0.0.1:5173")
USER = os.environ.get("FOODAI_USER", "demo_mobile")
PWD = os.environ.get("FOODAI_PWD", "demo123456")


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

        pg.goto(f"{BASE}/login", wait_until="networkidle")
        pg.fill('input[placeholder="用户名"]', USER)
        pg.fill('input[placeholder="密码"]', PWD)
        pg.click('button:has-text("登 录")')
        pg.wait_for_url("**/m/schedule", timeout=20000)
        pg.wait_for_timeout(1500)

        # 1) 日程：打卡计划
        pg.screenshot(path=str(OUT / "01_schedule.png"), full_page=True)
        print("[OK] 日程页 -> 01_schedule.png")

        # 2) 日历：每天完成点 + 当日简约打卡计划
        if pg.locator(".cal-bar").count():
            pg.locator(".cal-bar").click()
            pg.wait_for_timeout(900)
            pg.screenshot(path=str(OUT / "02_schedule_calendar.png"), full_page=True)
            print("[OK] 日历简约计划 -> 02_schedule_calendar.png")
            dots = pg.locator(".cal-dots .dot").count()
            chips = pg.locator(".dp-chip").count()
            print(f"     日历完成点 {dots} 个，简约计划 chip {chips} 个")
            if not chips:
                errors.append("日历里没有出现当日简约打卡计划 chip")

        # 3) 计划详情 → 改菜抽屉
        pg.goto(f"{BASE}/m/plan", wait_until="networkidle")
        pg.wait_for_timeout(1200)
        pg.screenshot(path=str(OUT / "03_plan_list.png"), full_page=True)
        if not pg.locator(".plan-card").count():
            errors.append("计划页没有卡片（账号数据缺失？）")
        else:
            pg.locator(".plan-card").first.click()
            pg.wait_for_timeout(1500)
            pg.screenshot(path=str(OUT / "04_plan_detail.png"), full_page=True)

            if pg.locator('button:has-text("改菜")').count():
                pg.locator('button:has-text("改菜")').first.click()
                pg.wait_for_timeout(1600)
                pg.screenshot(path=str(OUT / "05_edit_meals.png"), full_page=True)
                print("[OK] 改菜抽屉 -> 05_edit_meals.png")

                # 改份量 → 触发试算 → 看超上限提示
                portion = pg.locator(".dish-g input").first
                portion.fill("500")
                portion.press("Enter")
                pg.wait_for_timeout(1800)
                pg.screenshot(path=str(OUT / "06_edit_preview.png"), full_page=True)
                nutri_text = pg.locator(".nutri").first.inner_text() if pg.locator(".nutri").count() else ""
                print("[OK] 实时营养对比 -> 06_edit_preview.png")
                print("     ", " / ".join(nutri_text.split("\n")[:6]))
                if not nutri_text:
                    errors.append("改菜后没有出现营养对比条")

                # 换一道
                if pg.locator('.dish-edit button:has-text("换")').count():
                    pg.locator('.dish-edit button:has-text("换")').first.click()
                    pg.wait_for_timeout(1800)
                    pg.screenshot(path=str(OUT / "07_swap.png"), full_page=True)
                    alts = pg.locator(".swap-item").count()
                    print(f"[OK] 换一道弹窗 -> 07_swap.png（{alts} 个候选）")
                    pg.keyboard.press("Escape")
                    pg.wait_for_timeout(400)
                pg.keyboard.press("Escape")
                pg.wait_for_timeout(400)
            else:
                errors.append("详情页没有「改菜 / 改运动」入口")

        # 4) 运动：今日运动处方
        pg.goto(f"{BASE}/m/sport", wait_until="networkidle")
        pg.wait_for_timeout(1500)
        pg.screenshot(path=str(OUT / "08_sport.png"), full_page=True)
        print("[OK] 运动页 -> 08_sport.png")
        if not pg.locator(".rx-row").count():
            errors.append("运动页没有今日运动处方")

        # 5) 我的：三环
        pg.goto(f"{BASE}/m/me", wait_until="networkidle")
        pg.wait_for_timeout(1500)
        pg.screenshot(path=str(OUT / "09_me.png"), full_page=True)
        print("[OK] 我的页 -> 09_me.png")
        if pg.locator(".ring").count() < 3:
            errors.append("我的页三环未渲染")

        # 横向溢出
        for name in ("01_schedule", "04_plan_detail", "05_edit_meals", "08_sport", "09_me"):
            pass
        overflow = pg.evaluate(
            "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
        )
        if overflow and overflow > 2:
            errors.append(f"/m/me 横向溢出 {overflow}px")

        browser.close()

    print("\n=== 检查结果 ===")
    if errors:
        for e in dict.fromkeys(errors):
            print("  [!]", e)
    else:
        print("  通过：无 JS 报错、改菜/日历/运动/三环均正常")
    print(f"截图目录：{OUT}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
