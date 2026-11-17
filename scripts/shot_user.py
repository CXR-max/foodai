"""临时：以指定用户身份截手机端页面（跳过登录，直接注入令牌）

用法：python scripts/shot_user.py 13 /m/schedule sched.png [锚点文字]
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / ".tmp_shots" / "review"
BASE = "http://127.0.0.1:5173"
PY = ROOT / "backend" / ".venv" / "Scripts" / "python.exe"


def token_for(user_id: str) -> str:
    code = f"from app.core.security import create_token; print(create_token({user_id}))"
    return subprocess.check_output([str(PY), "-c", code], cwd=str(ROOT / "backend"),
                                   text=True).strip()


def main() -> int:
    user_id = sys.argv[1]
    path = sys.argv[2]
    name = sys.argv[3]
    anchor = sys.argv[4] if len(sys.argv) > 4 else ""
    token = token_for(user_id)
    OUT.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        ctx = browser.new_context(viewport={"width": 390, "height": 844},
                                  device_scale_factor=2, is_mobile=True, has_touch=True)
        ctx.add_init_script(f"window.localStorage.setItem('token', {token!r})")
        pg = ctx.new_page()
        pg.on("pageerror", lambda e: errors.append(f"JS: {e}"))
        pg.goto(f"{BASE}{path}", wait_until="networkidle")
        pg.wait_for_timeout(1800)
        if len(sys.argv) > 5:
            # 第 5 个参数：进入页面后依次点一下（用 | 分隔，例如进详情再展开某天）
            for sel in sys.argv[5].split("|"):
                if sel.strip():
                    pg.locator(sel.strip()).first.click()
                    pg.wait_for_timeout(1200)
        if anchor:
            pg.locator(f"text={anchor}").first.scroll_into_view_if_needed()
            pg.wait_for_timeout(700)
        pg.screenshot(path=str(OUT / name))
        print(f"[OK] {OUT / name}")
        if errors:
            print("[!]", errors[:3])
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
