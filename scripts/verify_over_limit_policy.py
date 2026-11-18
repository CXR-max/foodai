"""核对「超限默认处理」偏好：目标区开关可切换，弹窗按偏好预选且可临时改

前置：前后端已启动，账号里有至少一份方案（默认 demo_mobile）。
用法：backend\\.venv\\Scripts\\python.exe scripts\\verify_over_limit_policy.py [用户名] [密码]
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / ".tmp_shots" / "policy"
BASE = os.environ.get("FOODAI_WEB", "http://127.0.0.1:5173")
API = os.environ.get("FOODAI_API", "http://127.0.0.1:8000")


def set_policy(username: str, password: str, policy: str) -> None:
    with httpx.Client(base_url=API, timeout=30) as client:
        token = client.post("/api/auth/login", json={"username": username, "password": password}).json()["token"]
        resp = client.put("/api/profile", headers={"Authorization": f"Bearer {token}"},
                          json={"over_limit_policy": policy})
        resp.raise_for_status()
        print(f"[api] 偏好 -> {resp.json()['over_limit_policy']}")


def main() -> int:
    username = sys.argv[1] if len(sys.argv) > 1 else "demo_mobile"
    password = sys.argv[2] if len(sys.argv) > 2 else "demo123456"
    OUT.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []

    set_policy(username, password, "ask")

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        ctx = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2,
                                  is_mobile=True, has_touch=True)
        pg = ctx.new_page()
        pg.on("pageerror", lambda e: errors.append(f"JS 错误: {e}"))
        pg.on("console", lambda m: errors.append(f"控制台 {m.type}: {m.text}") if m.type == "error" else None)

        pg.goto(f"{BASE}/login", wait_until="networkidle")
        pg.fill('input[placeholder="用户名"]', username)
        pg.fill('input[placeholder="密码"]', password)
        pg.click('button:has-text("登 录")')
        pg.wait_for_url("**/m/schedule", timeout=20000)

        # 1) 详情页「每日目标」里的超限处理开关
        pg.goto(f"{BASE}/m/plan", wait_until="networkidle")
        pg.wait_for_timeout(1500)
        pg.locator(".plan-card").first.click()
        pg.wait_for_timeout(1600)
        if not pg.locator(".policy-card").count():
            errors.append("详情页没有「超限处理」开关")
        else:
            pg.locator(".policy-card").scroll_into_view_if_needed()
            pg.wait_for_timeout(400)
            pg.screenshot(path=str(OUT / "01_policy_card.png"), full_page=True)
            checked = pg.locator(".policy-group .el-radio-button.is-active").inner_text().strip()
            print(f"[OK] 开关当前选中：{checked} -> 01_policy_card.png")

        # 2) 切到「加运动」：弹窗默认预选加运动
        pg.locator(".policy-group .el-radio-button").nth(2).click()
        pg.wait_for_timeout(1200)
        checked = pg.locator(".policy-group .el-radio-button.is-active").inner_text().strip()
        if "加运动" not in checked:
            errors.append(f"切换后开关没生效（当前 {checked}）")
        print(f"[OK] 切到「{checked}」")

        # 3) 改菜改到超限 → 保存 → 弹窗默认选中加运动
        if pg.locator('button:has-text("改菜")').count():
            pg.locator('button:has-text("改菜")').first.click()
            pg.wait_for_timeout(1500)
            portion = pg.locator(".dish-g input").first
            portion.fill("500")
            portion.press("Enter")
            pg.wait_for_timeout(1800)
            pg.locator('.el-drawer button:has-text("保存")').first.click()
            pg.wait_for_timeout(1200)
            if not pg.locator(".warn-group").count():
                errors.append("超限后没有弹出处置弹窗")
            else:
                pg.screenshot(path=str(OUT / "02_warn_exercise_default.png"), full_page=True)
                picked = pg.locator(".warn-group .el-radio.is-checked").inner_text().strip().split("\n")[0]
                tip = pg.locator(".warn-policy").inner_text().strip()
                print(f"[OK] 弹窗默认选中：{picked}｜{tip}")
                if "加运动" not in picked:
                    errors.append(f"弹窗没有按偏好预选加运动（当前 {picked}）")

                # 4) 临时改成「就这样保存」，验证可改
                pg.locator(".warn-group .el-radio").nth(2).click()
                pg.wait_for_timeout(300)
                picked2 = pg.locator(".warn-group .el-radio.is-checked").inner_text().strip().split("\n")[0]
                pg.screenshot(path=str(OUT / "03_warn_override.png"), full_page=True)
                print(f"[OK] 临时改为：{picked2} -> 03_warn_override.png")
                if "就这样保存" not in picked2:
                    errors.append("弹窗里改不了处理方式")
                pg.locator('.el-dialog button:has-text("按此处理")').click()
                pg.wait_for_timeout(2000)
                if pg.locator(".el-message--success").count():
                    print(f"[OK] 保存成功：{pg.locator('.el-message--success').first.inner_text().strip()}")
        else:
            errors.append("详情页没有「改菜」入口")

        browser.close()

    # 恢复成手动选择，避免影响演示
    set_policy(username, password, "ask")

    print("\n=== 检查结果 ===")
    if errors:
        for e in dict.fromkeys(errors):
            print("  [!]", e)
    else:
        print("  通过：目标区开关可切换、弹窗按偏好预选、可临时改处理方式")
    print(f"截图目录：{OUT}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
