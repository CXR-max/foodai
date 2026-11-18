"""端到端核对「历史方案 → 导入日程」：方案置为进行中、其它归档、日期顺延到今天、当天可打卡

前置：前后端已启动。脚本自己造一个临时账号 + 两份方案（一份 15 天前的历史方案、一份今天开始的进行中），
跑完顺带截图。用法：backend\\.venv\\Scripts\\python.exe scripts\\verify_history_import.py
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / ".tmp_shots" / "history"
DB = ROOT / "backend" / "foodai.db"
BASE = os.environ.get("FOODAI_WEB", "http://127.0.0.1:5173")
API = os.environ.get("FOODAI_API", "http://127.0.0.1:8000")

MEALS = [
    {"meal_type": "breakfast", "name": "早餐", "calories": 330,
     "items": [{"name": "燕麦", "portion_g": 80}]},
    {"meal_type": "lunch", "name": "午餐", "calories": 520,
     "items": [{"name": "米饭", "portion_g": 200}, {"name": "鸡胸肉", "portion_g": 150}]},
    {"meal_type": "dinner", "name": "晚餐", "calories": 430,
     "items": [{"name": "米饭", "portion_g": 150}, {"name": "西蓝花", "portion_g": 200}]},
]
EXERCISES = [
    {"type": "快走", "duration_min": 40, "calories_burned": 240, "tips": "热身 5 分钟", "intensity": "moderate"},
    {"type": "拉伸", "duration_min": 10, "calories_burned": 25, "tips": "放松", "intensity": "light"},
]
TARGETS = {
    "calorie_target": 2000, "calorie_min": 1800, "calorie_max": 2100,
    "protein_min_g": 116, "water_min_ml": 2000, "sleep_min_h": 7.0,
    "exercise_burn_min": 200, "exercise_days_per_week": 5,
    "bedtime": "23:00", "wake_time": "07:00", "goal": "lose",
}
SLEEP = {"target_h": 7.0, "planned_h": 8.0, "bedtime": "23:00", "wake_at": "07:00", "tips": "早点睡"}


def seed() -> tuple[str, str, int, int]:
    username = f"hist_{int(time.time())}"
    password = "hist123456"
    with httpx.Client(base_url=API, timeout=30) as client:
        resp = client.post("/api/auth/register", json={
            "username": username, "password": password, "nickname": "历史方案验证",
        })
        resp.raise_for_status()
        token = resp.json()["token"]
        client.put("/api/profile", headers={"Authorization": f"Bearer {token}"}, json={
            "height_cm": 175, "weight_kg": 72.5, "age": 26, "gender": "male",
            "activity_level": "moderate", "goal": "lose",
            "sleep_time": "23:00", "wake_time": "07:00",
        })
        me = client.get("/api/profile", headers={"Authorization": f"Bearer {token}"}).json()
        assert me, "档案保存失败"

    with sqlite3.connect(DB) as conn:
        uid = conn.execute("select id from users where username=?", (username,)).fetchone()[0]
        conn.execute("update health_profiles set height_cm=175, weight_kg=72.5, age=26, gender='male', "
                     "activity_level='moderate', goal='lose', sleep_time='23:00', wake_time='07:00' "
                     "where user_id=?", (uid,))

        def add_plan(title: str, start: date, status: str, days: int) -> int:
            cur = conn.execute(
                "insert into diet_plans (user_id, title, status, start_date, summary, daily_target_calories, "
                "targets, provider, plan_version, created_at) values (?,?,?,?,?,?,?,?,?,datetime('now'))",
                (uid, title, status, start.isoformat(), "验证用方案", 2000,
                 json.dumps(TARGETS, ensure_ascii=False), "seed", 1),
            )
            pid = cur.lastrowid
            for i in range(1, days + 1):
                d = start + timedelta(days=i - 1)
                conn.execute(
                    "insert into plan_days (plan_id, day_index, date, meals, exercise, exercises, sleep, "
                    "water_ml, targets, estimated_calories, theme) values (?,?,?,?,?,?,?,?,?,?,?)",
                    (pid, i, d.isoformat(), json.dumps(MEALS, ensure_ascii=False),
                     json.dumps(EXERCISES[0], ensure_ascii=False), json.dumps(EXERCISES, ensure_ascii=False),
                     json.dumps(SLEEP, ensure_ascii=False), 2000,
                     json.dumps(TARGETS, ensure_ascii=False), 1280, "轻盈开局"),
                )
            return pid

        old_id = add_plan("历史方案（15 天前）", date.today() - timedelta(days=15), "archived", 3)
        current_id = add_plan("当前进行中方案", date.today(), "active", 3)
        conn.commit()

    return username, password, old_id, current_id


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    username, password, old_id, current_id = seed()
    print(f"[seed] {username} 历史方案={old_id} 当前方案={current_id}")

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        ctx = browser.new_context(
            viewport={"width": 390, "height": 844}, device_scale_factor=2,
            is_mobile=True, has_touch=True,
        )
        pg = ctx.new_page()
        pg.on("pageerror", lambda e: errors.append(f"JS 错误: {e}"))
        pg.on("console", lambda m: errors.append(f"控制台 {m.type}: {m.text}") if m.type == "error" else None)

        pg.goto(f"{BASE}/login", wait_until="networkidle")
        pg.fill('input[placeholder="用户名"]', username)
        pg.fill('input[placeholder="密码"]', password)
        pg.click('button:has-text("登 录")')
        pg.wait_for_url("**/m/schedule", timeout=20000)

        pg.goto(f"{BASE}/m/plan", wait_until="networkidle")
        pg.wait_for_timeout(1500)
        pg.screenshot(path=str(OUT / "01_plan_groups.png"), full_page=True)
        history_cards = pg.locator(".plan-card.history").count()
        print(f"[OK] 计划页：历史方案卡片 {history_cards} 份 -> 01_plan_groups.png")
        if history_cards < 1:
            errors.append("历史方案分组没有卡片")

        # 点历史卡片的「导入日程」→ 确认
        pg.locator(".plan-card.history button:has-text('导入日程')").first.click()
        pg.wait_for_timeout(800)
        pg.screenshot(path=str(OUT / "02_confirm.png"), full_page=True)
        pg.locator(".el-message-box button:has-text('导入')").last.click()
        try:
            pg.wait_for_url("**/m/schedule", timeout=20000)
        except Exception:
            errors.append("导入后没有跳到日程页")
        pg.wait_for_timeout(1800)
        pg.screenshot(path=str(OUT / "03_schedule_after_import.png"), full_page=True)

        chips = pg.locator(".dp-chip").count()
        tasks = pg.locator(".task").count()
        print(f"[OK] 导入后日程页：任务 {tasks} 条（日历 chip {chips}）")
        if tasks < 5:
            errors.append(f"导入后当天任务只有 {tasks} 条，日期可能没对齐")

        # 展开日历看简约计划
        if pg.locator(".cal-bar").count():
            pg.locator(".cal-bar").click()
            pg.wait_for_timeout(900)
            pg.screenshot(path=str(OUT / "04_calendar.png"), full_page=True)

        # 回到计划页：被导入的历史方案应在「进行中」并显示「已重新启用」
        pg.goto(f"{BASE}/m/plan", wait_until="networkidle")
        pg.wait_for_timeout(1500)
        pg.screenshot(path=str(OUT / "05_reactivated.png"), full_page=True)
        bar = pg.locator(".plan-card.active .reactivate-bar").first
        if bar.count():
            print(f"[OK] 计划页提示：{bar.inner_text().strip()}")
        else:
            errors.append("导入历史方案后，进行中的卡片没有「已重新启用」提示")

        browser.close()

    # 后端状态核对
    with httpx.Client(base_url=API, timeout=30) as client:
        token = client.post("/api/auth/login", json={"username": username, "password": password}).json()["token"]
        plans = client.get("/api/plans", headers={"Authorization": f"Bearer {token}"}).json()
        by_id = {p["id"]: p for p in plans}
        old, cur = by_id[old_id], by_id[current_id]
        print(f"[状态] 历史方案 -> {old['status']}（{old['start_date']} 起）")
        print(f"[状态] 当前方案 -> {cur['status']}")
        if old["status"] != "active":
            errors.append("导入的历史方案没有变成进行中")
        if cur["status"] != "archived":
            errors.append("原来的进行中方案没有归档")
        if old["start_date"] != str(date.today()):
            errors.append(f"历史方案日期没有顺延到今天（{old['start_date']}）")
        if not old.get("reactivated_at"):
            errors.append("历史方案重新启用后没有记下 reactivated_at")
        print(f"[状态] 重新启用标记 -> {old.get('reactivated_at')}（第 {old.get('reactivate_count')} 次）")

    print("\n=== 检查结果 ===")
    if errors:
        for e in dict.fromkeys(errors):
            print("  [!]", e)
    else:
        print("  通过：导入即进行中、旧方案归档、日期顺延到今天、当天任务可打卡")
    print(f"截图目录：{OUT}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
