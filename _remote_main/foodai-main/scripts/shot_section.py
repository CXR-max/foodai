"""临时：滚到指定区块再截一屏（内部滚动容器 full_page 截不全）"""
from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / ".tmp_shots" / "review"
BASE = "http://127.0.0.1:5173"


def main() -> int:
    path = sys.argv[1]
    anchor = sys.argv[2]
    name = sys.argv[3]
    OUT.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        ctx = browser.new_context(viewport={"width": 390, "height": 844},
                                  device_scale_factor=2, is_mobile=True, has_touch=True)
        pg = ctx.new_page()
        pg.on("pageerror", lambda e: errors.append(f"JS: {e}"))
        pg.goto(f"{BASE}/login", wait_until="networkidle")
        pg.fill('input[placeholder="用户名"]', "demo_mobile")
        pg.fill('input[placeholder="密码"]', "demo123456")
        pg.click('button:has-text("登 录")')
        pg.wait_for_url("**/m/schedule", timeout=20000)
        pg.goto(f"{BASE}{path}", wait_until="networkidle")
        pg.wait_for_timeout(1500)
        if anchor:
            pg.locator(f"text={anchor}").first.scroll_into_view_if_needed()
            pg.wait_for_timeout(600)
        if len(sys.argv) > 4:
            pg.locator(sys.argv[4]).first.click()
            pg.wait_for_timeout(700)
        pg.screenshot(path=str(OUT / name))
        print(f"[OK] {OUT / name}")
        if errors:
            print("[!]", errors[:3])
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
