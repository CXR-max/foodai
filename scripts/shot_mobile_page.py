"""小工具：用手机视口截一张页面，核对布局用（不改数据）

用法：backend\\.venv\\Scripts\\python.exe scripts/shot_mobile_page.py <用户名> <密码> <路径> <输出名> [detail]
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / ".tmp_shots" / "review"
BASE = os.environ.get("FOODAI_WEB", "http://127.0.0.1:5173")


def main() -> int:
    username = sys.argv[1]
    password = sys.argv[2]
    path = sys.argv[3] if len(sys.argv) > 3 else "/m/plan"
    name = sys.argv[4] if len(sys.argv) > 4 else "tmp.png"
    click_card = len(sys.argv) > 5 and sys.argv[5] == "detail"
    OUT.mkdir(parents=True, exist_ok=True)

    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        ctx = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2,
                                  is_mobile=True, has_touch=True)
        pg = ctx.new_page()
        pg.on("pageerror", lambda e: errors.append(f"JS: {e}"))
        pg.goto(f"{BASE}/login", wait_until="networkidle")
        pg.fill('input[placeholder="用户名"]', username)
        pg.fill('input[placeholder="密码"]', password)
        pg.click('button:has-text("登 录")')
        pg.wait_for_url("**/m/schedule", timeout=20000)
        pg.goto(f"{BASE}{path}", wait_until="networkidle")
        pg.wait_for_timeout(1800)
        if click_card:
            pg.locator(".plan-card.active").first.click()
            pg.wait_for_timeout(1800)
        pg.screenshot(path=str(OUT / name), full_page=True)
        print(f"[OK] {OUT / name}")
        if errors:
            print("[!]", errors[:3])
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
