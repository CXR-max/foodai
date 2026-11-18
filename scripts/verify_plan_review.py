"""造「方案执行评价」演示数据并核对：历史方案卡片显示评分、详情页评价板块、重新启用的蓝色弹窗

前置：前后端已启动。脚本给 demo 账号造两份已归档方案（一份执行较好、一份基本没执行），
带打卡/饮食/睡眠/饮水记录，然后截图核对；最后把「上周方案」重新导入，
让蓝色「已重新启用」弹窗出现在截图里（另一份历史方案保留给用户自己点）。

用法：backend\\.venv\\Scripts\\python.exe scripts\\verify_plan_review.py [用户名] [密码]
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / ".tmp_shots" / "review"
DB = ROOT / "backend" / "foodai.db"
BASE = os.environ.get("FOODAI_WEB", "http://127.0.0.1:5173")
API = os.environ.get("FOODAI_API", "http://127.0.0.1:8000")

MEALS = [
    {"meal_type": "breakfast", "name": "早餐", "calories": 330,
     "items": [{"name": "燕麦", "portion_g": 80}, {"name": "纯牛奶", "portion_g": 250}]},
    {"meal_type": "lunch", "name": "午餐", "calories": 620,
     "items": [{"name": "米饭", "portion_g": 200}, {"name": "鸡胸肉", "portion_g": 150}]},
    {"meal_type": "dinner", "name": "晚餐", "calories": 520,
     "items": [{"name": "米饭", "portion_g": 150}, {"name": "西蓝花", "portion_g": 200}]},
]
EXERCISES = [
    {"type": "快走", "duration_min": 40, "calories_burned": 240, "tips": "饭后 1 小时", "intensity": "moderate"},
    {"type": "拉伸", "duration_min": 10, "calories_burned": 25, "tips": "睡前放松", "intensity": "light"},
]
SLEEP = {"target_h": 7.0, "planned_h": 8.0, "bedtime": "23:00", "wake_at": "07:00", "tips": "别刷手机"}
TARGETS = {
    "calorie_target": 2000, "calorie_min": 1800, "calorie_max": 2100,
    "protein_min_g": 116, "water_min_ml": 2000, "sleep_min_h": 7.0,
    "exercise_burn_min": 200, "exercise_days_per_week": 5,
    "bedtime": "23:00", "wake_time": "07:00", "goal": "lose",
}
TASKS = [("meal_breakfast", "diet"), ("meal_lunch", "diet"), ("meal_dinner", "diet"),
         ("exercise", "exercise"), ("sleep", "sleep"), ("water", "water")]


def seed(username: str) -> tuple[int, int]:
    with sqlite3.connect(DB) as conn:
        uid = conn.execute("select id from users where username=?", (username,)).fetchone()[0]

        def add_plan(title: str, start: date, days: int, done_pattern: list[int]) -> int:
            cur = conn.execute(
                "insert into diet_plans (user_id, title, status, start_date, summary, daily_target_calories, "
                "targets, provider, plan_version, created_at) values (?,?,?,?,?,?,?,?,?,datetime('now'))",
                (uid, title, "archived", start.isoformat(), "上一期执行的方案", 2000,
                 json.dumps(TARGETS, ensure_ascii=False), "seed", 1),
            )
            pid = cur.lastrowid
            for i in range(1, days + 1):
                d = start + timedelta(days=i - 1)
                edited = i == 2
                day_targets = dict(TARGETS)
                if edited:
                    day_targets["nutrition_snapshot"] = {"calories": 2050, "status": "ok"}
                    day_targets["calorie_source"] = "manual_edit"
                cur2 = conn.execute(
                    "insert into plan_days (plan_id, day_index, date, meals, exercise, exercises, sleep, "
                    "water_ml, targets, estimated_calories, theme, edit_count, last_edited_at) "
                    "values (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (pid, i, d.isoformat(), json.dumps(MEALS, ensure_ascii=False),
                     json.dumps(EXERCISES[0], ensure_ascii=False), json.dumps(EXERCISES, ensure_ascii=False),
                     json.dumps(SLEEP, ensure_ascii=False), 2000,
                     json.dumps(day_targets, ensure_ascii=False), 1470, "轻盈开局",
                     1 if edited else 0, "2026-11-11 20:10:00" if edited else None),
                )
                day_id = cur2.lastrowid
                done = done_pattern[i - 1]
                for idx, (key, ctype) in enumerate(TASKS):
                    conn.execute(
                        "insert into check_ins (user_id, check_date, check_type, task_key, status, "
                        "plan_day_id, calories_burned, created_at) values (?,?,?,?,?,?,?,datetime('now'))",
                        (uid, d.isoformat(), ctype, key, "done" if idx < done else "pending",
                         day_id, 240 if key == "exercise" and idx < done else None),
                    )
                # 实际记录：有一点超、有睡不够、水喝少了
                intake = [2050, 2280, 1600][i - 1]
                conn.execute(
                    "insert into food_records (user_id, dish_name, calories, eaten_date, eaten_at, source) "
                    "values (?,?,?,?,?,?)",
                    (uid, "实测餐", intake, d.isoformat(), f"{d.isoformat()} 12:30:00", "manual"),
                )
                conn.execute(
                    "insert or ignore into sleep_logs (user_id, sleep_date, duration_min, source) values (?,?,?,?)",
                    (uid, d.isoformat(), [450, 390, 420][i - 1], "manual"),
                )
                conn.execute(
                    "insert into water_logs (user_id, log_date, ml, source) values (?,?,?,?)",
                    (uid, d.isoformat(), [2200, 1800, 1500][i - 1], "manual"),
                )
            return pid

        # 上月那两份：一份执行挺认真，一份基本没动
        good_id = add_plan(f"上周方案 {date.today() - timedelta(days=10):%m-%d}",
                           date.today() - timedelta(days=10), 3, [6, 4, 3])
        idle_id = add_plan(f"上上周方案 {date.today() - timedelta(days=24):%m-%d}",
                           date.today() - timedelta(days=24), 3, [0, 0, 0])
        conn.commit()
    return good_id, idle_id


def main() -> int:
    username = sys.argv[1] if len(sys.argv) > 1 else "demo_mobile"
    password = sys.argv[2] if len(sys.argv) > 2 else "demo123456"
    OUT.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []

    good_id, idle_id = seed(username)
    print(f"[seed] 方案 {good_id}（认真执行）/ {idle_id}（基本没动）")

    with httpx.Client(base_url=API, timeout=30) as client:
        token = client.post("/api/auth/login", json={"username": username, "password": password}).json()["token"]
        headers = {"Authorization": f"Bearer {token}"}
        for pid in (good_id, idle_id):
            review = client.get(f"/api/plans/{pid}/review", headers=headers).json()
            print(f"[评价] 方案 {pid}: {review.get('grade')} {review.get('score')} 分 · {review.get('headline')}")
        if not client.get(f"/api/plans/{good_id}/review", headers=headers).json().get("score"):
            errors.append("认真执行的方案没算出分数")

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

        # 1) 历史卡片带评分
        pg.goto(f"{BASE}/m/plan", wait_until="networkidle")
        pg.wait_for_timeout(1600)
        pg.screenshot(path=str(OUT / "01_history_review.png"), full_page=True)
        cards = pg.locator(".plan-card.history .pc-review").count()
        print(f"[OK] 历史卡片评价条 {cards} 个 -> 01_history_review.png")
        if cards < 1:
            errors.append("历史方案卡片没有显示评价")

        # 2) 详情页「本期评价」
        pg.locator(".plan-card.history").first.click()
        pg.wait_for_timeout(1800)
        pg.screenshot(path=str(OUT / "02_review_detail.png"), full_page=True)
        if pg.locator(".review-card").count():
            score = pg.locator(".review-score b").first.inner_text().strip()
            print(f"[OK] 详情页评价 {score} 分 -> 02_review_detail.png")
        else:
            errors.append("详情页没有「本期评价」板块")

        # 3) 重新启用：历史方案 → 导入日程 → 蓝色弹窗
        pg.goto(f"{BASE}/m/plan", wait_until="networkidle")
        pg.wait_for_timeout(1500)
        pg.locator(".plan-card.history button:has-text('导入日程')").first.click()
        pg.wait_for_timeout(700)
        pg.locator(".el-message-box button:has-text('导入')").last.click()
        pg.wait_for_timeout(1800)
        if pg.locator(".el-message-box").count():
            pg.screenshot(path=str(OUT / "03_reactivate_dialog.png"), full_page=True)
            text = pg.locator(".el-message-box__message").first.inner_text().strip()
            print(f"[OK] 蓝色弹窗：{text[:40]}…")
            if "重新启用" not in text:
                errors.append("弹窗里没写重新启用")
            pg.locator(".el-message-box button:has-text('知道了')").click()
            pg.wait_for_timeout(1200)
        else:
            errors.append("导入历史方案没有弹出「已重新启用」提示框")

        pg.goto(f"{BASE}/m/plan", wait_until="networkidle")
        pg.wait_for_timeout(1500)
        pg.screenshot(path=str(OUT / "04_after_reactivate.png"), full_page=True)
        if not pg.locator(".plan-card.active .reactivate-bar").count():
            errors.append("进行中卡片没显示「已重新启用」提示条")

        browser.close()

    # 状态核对：原进行中被归档且带评价
    with httpx.Client(base_url=API, timeout=30) as client:
        token = client.post("/api/auth/login", json={"username": username, "password": password}).json()["token"]
        plans = client.get("/api/plans", headers={"Authorization": f"Bearer {token}"}).json()
    active = [p for p in plans if p["status"] == "active"]
    archived = [p for p in plans if p["status"] != "active"]
    print(f"[状态] 进行中 {len(active)} 份、历史 {len(archived)} 份")
    if len(active) != 1:
        errors.append(f"进行中方案应只有 1 份，实际 {len(active)}")
    if not any(p.get("review_score") is not None for p in archived):
        errors.append("被顶掉的历史方案没有留下评价")

    print("\n=== 检查结果 ===")
    if errors:
        for e in dict.fromkeys(errors):
            print("  [!]", e)
    else:
        print("  通过：历史卡片有评分、详情有评价板块、重新启用弹出蓝色提示、旧方案归档带评价")
    print(f"截图目录：{OUT}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
