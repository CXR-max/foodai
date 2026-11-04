"""临时校验：用本地千问走一遍 注册→档案→逐天流式生成方案"""
import json
import sys
import time

import httpx

BASE = "http://127.0.0.1:8000/api"
sys.stdout.reconfigure(encoding="utf-8")
c = httpx.Client(base_url=BASE, timeout=300)

uniq = str(int(time.time()))
r = c.post("/auth/register", json={"username": f"localtest_{uniq}", "password": "test123456", "nickname": "本地模型验证"})
print("注册:", r.status_code)
token = r.json()["token"]
c.headers["Authorization"] = f"Bearer {token}"

r = c.put("/profile", json={
    "height_cm": 175, "weight_kg": 70, "age": 25, "gender": "male",
    "activity_level": "moderate", "goal": "lose",
    "taste_preferences": ["清淡"], "dietary_restrictions": ["忌辣"],
    "exercise_preferences": ["跑步"],
})
print("档案:", r.status_code, "热量目标", r.json().get("daily_calorie_target"))

t0 = time.time()
day = None
with c.stream("POST", "/plans/generate-stream", json={"days": 2, "preferences": ["清淡"]}) as resp:
    print("流式接口:", resp.status_code)
    for line in resp.iter_lines():
        if not line or not line.startswith("data:"):
            continue
        payload = json.loads(line[5:].strip())
        if "theme" in payload:
            print(f"  ✓ 第{payload.get('day_index')}天 主题={payload.get('theme')} "
                  f"餐数={len(payload.get('meals') or [])}")
            day = payload
        elif "plan_id" in payload:
            print("  ✓ 已保存 plan_id =", payload["plan_id"], "积分 +", payload.get("points_awarded"))
elapsed = time.time() - t0

if day:
    meals = day.get("meals", [])
    print(f"生成成功：用时 {elapsed:.0f}s，最后一天主题「{day.get('theme')}」，{len(meals)} 餐")
    for m in meals:
        items = "、".join(f"{i.get('name')}{i.get('portion_g')}g" for i in m.get("items", []))
        print(f"  - {m.get('meal_type')}: {items}  {m.get('calories')}kcal")
    print("  运动:", day.get("exercise"))
else:
    print(f"未拿到结果，用时 {elapsed:.0f}s")
