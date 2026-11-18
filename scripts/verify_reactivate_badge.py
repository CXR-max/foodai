"""核对「曾重新启用」标记：重新启用的方案被再次归档后，历史卡片上要留痕迹

前置：前后端已启动，且已有 verify_history_import.py 造过的账号（脚本自己登录该账号再倒一次）。
用法：backend\\.venv\\Scripts\\python.exe scripts/verify_reactivate_badge.py <username> <password>
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / ".tmp_shots" / "history"
BASE = os.environ.get("FOODAI_WEB", "http://127.0.0.1:5173")
API = os.environ.get("FOODAI_API", "http://127.0.0.1:8000")


def main() -> int:
    username = sys.argv[1] if len(sys.argv) > 1 else "demo_mobile"
    password = sys.argv[2] if len(sys.argv) > 2 else "demo123456"
    OUT.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []

    with httpx.Client(base_url=API, timeout=30) as client:
        token = client.post("/api/auth/login", json={"username": username, "password": password}).json()["token"]
        headers = {"Authorization": f"Bearer {token}"}
        plans = client.get("/api/plans", headers=headers).json()

    active = next((p for p in plans if p["status"] == "active"), None)
    history = [p for p in plans if p["status"] != "active"]
    if not active or not history:
        print("[!] 该账号还没有「进行中 + 历史」两份方案，先跑 verify_history_import.py")
        return 1
    if not active.get("reactivated_at"):
        print("[!] 进行中的方案还不是「重新启用」来的，先跑 verify_history_import.py")
        return 1
    prev_active_id = active["id"]

    # 把另一份历史方案导入 → 刚才那份（重新启用过的）被归档，历史卡片上应留「曾重新启用」
    target = history[0]
    with httpx.Client(base_url=API, timeout=30) as client:
        token = client.post("/api/auth/login", json={"username": username, "password": password}).json()["token"]
        headers = {"Authorization": f"Bearer {token}"}
        resp = client.post(f"/api/plans/{target['id']}/import", headers=headers, json={"mode": "week"})
        print("[api]", resp.status_code, resp.json().get("message", "")[:60])
        plans = client.get("/api/plans", headers=headers).json()

    archived_with_flag = [p for p in plans if p["status"] != "active" and p.get("reactivated_at")]
    print(f"[状态] 归档且带重新启用痕迹：{[p['id'] for p in archived_with_flag]}")
    if not any(p["id"] == prev_active_id for p in archived_with_flag):
        errors.append(f"原进行中方案 {prev_active_id} 归档后丢了 reactivated_at")

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        ctx = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2,
                                  is_mobile=True, has_touch=True)
        pg = ctx.new_page()
        pg.on("pageerror", lambda e: errors.append(f"JS 错误: {e}"))
        pg.goto(f"{BASE}/login", wait_until="networkidle")
        pg.fill('input[placeholder="用户名"]', username)
        pg.fill('input[placeholder="密码"]', password)
        pg.click('button:has-text("登 录")')
        pg.wait_for_url("**/m/schedule", timeout=20000)
        pg.goto(f"{BASE}/m/plan", wait_until="networkidle")
        pg.wait_for_timeout(1500)
        pg.screenshot(path=str(OUT / "06_history_reactivated_flag.png"), full_page=True)

        flag = pg.locator(".plan-card.history .reactivate-flag").first
        if flag.count():
            print(f"[OK] 历史卡片标记：{flag.inner_text().strip()}")
        else:
            errors.append("历史卡片没有「曾重新启用」标记")
        browser.close()

    print("\n=== 检查结果 ===")
    print("  " + ("通过：历史卡片保留「曾重新启用」痕迹" if not errors else "有问题"))
    for e in dict.fromkeys(errors):
        print("  [!]", e)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
