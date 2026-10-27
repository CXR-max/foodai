"""
端到端自测脚本：跑通六大功能 + 商城的完整链路
用法：先启动后端（uvicorn app.main:app --port 8000），再执行 python e2e_test.py
每次运行会注册一个新用户（tester_时间戳），可重复执行
"""
import sys
import time

import httpx

BASE = "http://localhost:8000/api"
sys.stdout.reconfigure(encoding="utf-8")

c = httpx.Client(base_url=BASE, timeout=150)
uniq = str(int(time.time()))
ok_count = 0


def check(name: str, cond: bool, extra: str = ""):
    global ok_count
    assert cond, f"✗ {name} 失败 {extra}"
    ok_count += 1
    print(f"✓ {name} {extra}")


# 1. 注册登录
r = c.post("/auth/register", json={"username": f"tester_{uniq}", "password": "test123456", "nickname": "测试员"})
check("注册", r.status_code == 200, r.text[:100] if r.status_code != 200 else "")
token = r.json()["token"]
c.headers["Authorization"] = f"Bearer {token}"

# 2. 健康档案：BMI/热量目标/首次完善+20分
r = c.put("/profile", json={
    "height_cm": 175, "weight_kg": 70, "age": 25, "gender": "male",
    "activity_level": "moderate", "goal": "lose",
    "taste_preferences": ["清淡"], "dietary_restrictions": ["忌辣"],
    "exercise_preferences": ["跑步", "游泳"],
})
check("档案 BMI=22.9", r.json()["bmi"] == 22.9)
check("每日热量目标=2194", r.json()["daily_calorie_target"] == 2194)
check("完善档案 +20分", r.json()["points_awarded"] == 20)

# 3. 食物识别（mock）：营养来自库 + 同图同结果
img = b"\xff\xd8fake-jpeg-bytes-for-mock-test"
r = c.post("/recognitions", files={"file": ("test.jpg", img, "image/jpeg")})
rec = r.json()
check("识别 calories>0 且 provider=mock", rec["provider"] == "mock" and rec["calories"] > 0,
      f"→ {rec['dish_name']} {rec['calories']}kcal")
r2 = c.post("/recognitions", files={"file": ("test.jpg", img, "image/jpeg")}).json()
check("同图同结果（演示可复现）", r2["dish_name"] == rec["dish_name"])

# 4. 保存为饮食记录 + 防重复 409
r = c.post(f"/recognitions/{rec['id']}/save", json={"meal_type": "lunch"})
check("保存饮食记录", r.status_code == 200)
check("重复保存 409", c.post(f"/recognitions/{rec['id']}/save", json={"meal_type": "lunch"}).status_code == 409)
day = c.get("/food-records").json()
check("当日记录汇总", day["summary"]["total_calories"] > 0)

# 5. AI 方案（7 天，编辑第3天）
r = c.post("/plans/generate", json={"days": 7, "note": "想清淡一点"})
plan = r.json()
check("方案 7 天", len(plan["days"]) == 7, f"provider={plan['provider']} 目标{plan['daily_target_calories']}kcal")
check("每餐都有热量", all(m["calories"] > 0 for d in plan["days"] for m in d["meals"]))
d3 = plan["days"][2]
meals = d3["meals"]
meals[0]["items"][0]["name"] = "米饭"
meals[0]["items"][0]["portion_g"] = 500
r = c.put(f"/plans/{plan['id']}/days/{d3['day_index']}", json={"meals": meals}).json()
check("编辑方案第3天生效", r["meals"][0]["items"][0]["name"] == "米饭")
check("编辑后热量按营养库重算", r["estimated_calories"] == round(sum(m["calories"] for m in r["meals"])),
      f"{d3['estimated_calories']} → {r['estimated_calories']}")

# 6. 导入日历 + 逐项打卡（完成度积分）
r = c.post("/check-ins/from-plan", json={"plan_day_id": d3["id"]})
check("导入日历 4~5 项任务", 4 <= len(r.json()["created"]) <= 5, r.json()["message"])
check("重复导入幂等", c.post("/check-ins/from-plan", json={"plan_day_id": d3["id"]}).json()["created"] == [])
day_view = c.get("/check-ins", params={"date": d3["date"]}).json()
check("日历当日任务数", day_view["summary"]["total"] >= 4)
earned = sum(c.post(f"/check-ins/{i['id']}/complete").json()["points_awarded"] for i in day_view["items"])
check("逐项打卡（含全清奖励）", earned >= 4 * 3 + 10, f"+{earned} 分")
check("月历打点", c.get("/check-ins", params={"month": d3["date"][:7]}).status_code == 200)

# 7. 手动打卡 + 体重
check("手动运动打卡", c.post("/check-ins", json={"check_type": "exercise", "exercise_type": "跑步", "duration_min": 30}).status_code == 200)
w = c.post("/profile/weights", json={"weight_kg": 69.5}).json()
check("记体重 +2", w["points_awarded"] == 2)

# 8. 统计
ov = c.get("/stats/overview").json()
check("总览：今日热量/排名/连打", ov["today_calories"] > 0 and ov["rank"] >= 1 and ov["streak_days"] >= 1,
      f"今日{ov['today_calories']}kcal 排名{ov['rank']} 连打{ov['streak_days']}天 积分{ov['points_total']}")
check("热量趋势 14 天", len(c.get("/stats/calories", params={"days": 14}).json()) == 14)
check("体重轨迹", c.get("/stats/weight", params={"days": 90}).status_code == 200)
check("营养结构", c.get("/stats/nutrition", params={"days": 7}).status_code == 200)

# 9. 排行榜 + 积分商城
lb = c.get("/points/leaderboard").json()
check("排行榜实时", len(lb) >= 1 and lb[0]["points_total"] >= lb[-1]["points_total"])
rewards = c.get("/rewards").json()
check("商城 10 个奖励", len(rewards) == 10)
target50 = next(r for r in rewards if r["points_cost"] == 50)
r = c.post(f"/rewards/{target50['id']}/redeem").json()
check("兑换成功得核销码", bool(r.get("code")), f"→ {r.get('code')} 余{r.get('points_total')}分")
target80 = next(r for r in rewards if r["points_cost"] == 80)
check("积分不足 400", c.post(f"/rewards/{target80['id']}/redeem").status_code == 400)
check("我的兑换记录", len(c.get("/rewards/redemptions").json()) == 1)

print(f"\n全部通过：{ok_count} 项断言 ✓")
