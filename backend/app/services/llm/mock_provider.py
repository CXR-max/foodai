"""
MockProvider：零成本全流程模拟（默认 Provider，无需任何 API key）

识别逻辑：图片 sha256 哈希 → 确定性从营养库选菜（同一张图永远同一结果，演示可复现）
方案逻辑：规则版"营养师"——按热量目标/偏好/禁忌轮转生成 7 天计划
"""
import hashlib

from app.services.llm import foods_data
from app.services.llm.base import LLMProvider
from app.services.llm.prompts import EXERCISE_METABOLIC
from app.services.llm.schemas import (
    PlanDay, PlanExercise, PlanMeal, PlanMealItem, PlanResult,
    RecognizedIngredient, VisionRecognitionResult,
)

# 禁忌标签映射：勾了某个禁忌 → 营养库里带这些标签的菜不能选
RESTRICTION_TAGS = {
    "忌辣": {"偏辣"},
    "低糖": {"偏甜"},
    "海鲜过敏": {"海鲜"},
    "乳糖不耐": {"含乳"},
}
VEGETARIAN_TAG = "素食"


class MockProvider(LLMProvider):
    name = "mock"

    # ---------------- 食物识别（模拟） ----------------

    def recognize_food(self, image_bytes: bytes, content_type: str, profile_ctx: dict | None = None):
        # 1) 图片哈希 → 确定性随机源（同一张图结果恒定，演示可复现）
        h = int(hashlib.sha256(image_bytes).hexdigest(), 16)

        # 2) 按用户禁忌硬过滤菜池
        pool = foods_data.load_foods()
        restrictions = (profile_ctx or {}).get("dietary_restrictions", []) or []
        if "素食" in restrictions:
            pool = [f for f in pool if VEGETARIAN_TAG in (f.get("dietary_tags") or [])]
        for r in restrictions:
            bad = RESTRICTION_TAGS.get(r)
            if bad:
                pool = [f for f in pool if not (bad & set(f.get("dietary_tags") or []))]
        if not pool:
            pool = [f for f in foods_data.load_foods() if VEGETARIAN_TAG in (f.get("dietary_tags") or [])]

        # 3) 偏好优先：有 70% 概率从"符合口味偏好"的子集里选（体现 PPT 的"偏好优先"卖点）
        tastes = (profile_ctx or {}).get("taste_preferences", []) or []
        preferred = [f for f in pool if set(f.get("taste_tags") or []) & set(tastes)]
        if preferred and h % 10 < 7:
            pool = preferred

        # 4) 确定性选菜 + 分量 ±10% 抖动
        dish = pool[h % len(pool)]
        portion = round(dish["common_portion_g"] * (0.9 + (h >> 8) % 21 / 100))
        ingredients = [
            RecognizedIngredient(name=ing, portion_g=round(portion / max(len(dish["ingredients"]), 1)))
            for ing in dish.get("ingredients", [])
        ]
        return VisionRecognitionResult(
            is_food=True,
            dish_name=dish["name"],
            ingredients=ingredients,
            portion_g=portion,
            portion_desc=f"一份约{portion}g",
            confidence=round(0.75 + h % 20 / 100, 2),
            description=f"识别到常见中餐：{dish['name']}（mock 模拟结果，营养值由本地营养库计算）",
        )

    # ---------------- 方案生成（规则模拟） ----------------

    def generate_plan(self, profile_ctx: dict, days: int, note: str = ""):
        h = int(hashlib.sha256(str(profile_ctx).encode("utf-8")).hexdigest(), 16)
        target = int(profile_ctx.get("daily_target_calories") or 2000)

        # 菜池：按禁忌过滤
        pool = foods_data.load_foods()
        restrictions = profile_ctx.get("dietary_restrictions", []) or []
        if "素食" in restrictions:
            pool = [f for f in pool if VEGETARIAN_TAG in (f.get("dietary_tags") or [])]
        for r in restrictions:
            bad = RESTRICTION_TAGS.get(r)
            if bad:
                pool = [f for f in pool if not (bad & set(f.get("dietary_tags") or []))]

        # 各餐候选池（suitable_meals 命中优先，其次按类别兜底）
        def pick_pool(meal_type: str) -> list[dict]:
            exact = [f for f in pool if meal_type in (f.get("suitable_meals") or [])]
            if exact:
                return exact
            by_cat = {
                "breakfast": ["早餐", "主食", "汤粥"],
                "lunch": ["荤菜", "素菜", "主食"],
                "dinner": ["素菜", "荤菜", "汤粥"],
                "snack": ["水果", "饮品"],
            }.get(meal_type, ["主食"])
            sub = [f for f in pool if f.get("category") in by_cat]
            return sub or pool

        tastes = set(profile_ctx.get("taste_preferences", []) or [])

        def sort_preferred(items: list[dict], seed: int) -> list[dict]:
            """偏好菜排前 + 同偏好级内确定性轮转（尽量 7 天不重样）"""
            preferred = [f for f in items if set(f.get("taste_tags") or []) & tastes]
            normal = [f for f in items if f not in preferred]
            return preferred + normal[seed % len(normal):] + normal[:seed % len(normal)]

        plan_days: list[PlanDay] = []
        for day_i in range(1, days + 1):
            # 渐进式：day1 热量比目标高 15%，逐日收敛到目标
            scale = 1 + 0.15 * (days - day_i) / max(days - 1, 1)
            day_target = int(target * scale)
            meals: list[PlanMeal] = []
            # 三餐热量配比 30% / 40% / 25%，加餐 5%
            for meal_type, ratio in [("breakfast", 0.30), ("lunch", 0.40), ("dinner", 0.25), ("snack", 0.05)]:
                cands = sort_preferred(pick_pool(meal_type), h + day_i * 7 + hash(meal_type) % 13)
                meal_cal = day_target * ratio
                # 每餐选 1~2 道菜
                main = cands[(day_i + len(meals)) % len(cands)]
                items = [PlanMealItem(name=main["name"], portion_g=main["common_portion_g"])]
                total = main["per_100g"]["calories"] * main["common_portion_g"] / 100
                if meal_type in ("lunch", "dinner"):
                    # 午/晚餐主食：优先米饭/面条类（排除面包类），更符合中餐习惯
                    staple_pool = [f for f in pool
                                   if f.get("category") == "主食"
                                   and "面包" not in f["name"]
                                   and meal_type in (f.get("suitable_meals") or [])]
                    staple_pool = staple_pool or [f for f in pool if f.get("category") == "主食" and "面包" not in f["name"]] or pool
                    staple = staple_pool[day_i % len(staple_pool)]
                    items.append(PlanMealItem(name=staple["name"], portion_g=staple["common_portion_g"]))
                    total += staple["per_100g"]["calories"] * staple["common_portion_g"] / 100
                meals.append(PlanMeal(
                    meal_type=meal_type,
                    name=f"{'、'.join(i.name for i in items)}",
                    items=items,
                    calories=round(total),
                ))
                del meal_cal

            # 运动：从偏好里选，没有偏好就轮换快走/瑜伽；时长逐日 +5min（渐进式）
            ex_prefs = [e for e in (profile_ctx.get("exercise_preferences", []) or []) if e in EXERCISE_METABOLIC]
            ex_type = ex_prefs[(day_i - 1) % len(ex_prefs)] if ex_prefs else ["快走", "瑜伽", "骑行"][day_i % 3]
            duration = min(20 + 5 * (day_i - 1), 60)
            weight = profile_ctx.get("weight_kg") or 60
            burned = int(EXERCISE_METABOLIC.get(ex_type, 5) * duration * weight / 60)
            exercise = PlanExercise(
                type=ex_type, duration_min=duration, calories_burned=burned,
                tips=f"循序渐进，注意热身；当天感到疲劳可适当降低强度",
            )

            plan_days.append(PlanDay(
                day_index=day_i,
                theme=["轻盈开局", "循序渐近", "渐入佳境", "稳步推进", "活力满格", "接近目标", "习惯养成"][ (day_i - 1) % 7 ],
                meals=meals,
                exercise=exercise,
            ))

        return PlanResult(
            summary=f"已按你的口味偏好与热量目标（约{target}千卡/日）生成渐进式方案（mock 规则模拟）",
            daily_target_calories=target,
            days=plan_days,
        )

    # ---------------- 逐天流式生成（模拟） ----------------

    def generate_day(self, profile_ctx: dict, day_index: int, days: int,
                     prior_days: list[dict] | None = None, note: str = ""):
        # mock 直接生成到第 day_index 天，取最后一天返回
        return self.generate_plan(profile_ctx, day_index, note).days[-1]

    # ---------------- 生成方案前的纯文字沟通（模拟） ----------------

    def chat(self, messages: list[dict]) -> str:
        last = next((m.get("content", "") for m in reversed(messages) if m.get("role") == "user"), "")
        snippet = last[:20] + ("…" if len(last) > 20 else "")
        return (
            f"收到你说的「{snippet}」啦～为了帮你定制更合适的方案，再告诉我："
            "平时口味偏清淡还是重口？有没有忌口（辣/海鲜/乳糖/素食等）？喜欢什么运动？"
            "（当前为 mock 模拟回复，接入真实模型后对话会更智能）"
        )

    def chat_stream(self, messages: list[dict]):
        # mock 无真实流式，整段一次性产出
        yield self.chat(messages)

    # ---------------- 方案文字微调（模拟） ----------------

    def revise_plan(self, profile_ctx: dict, current_plan: dict, instruction: str,
                    day_index: int | None = None):
        # mock 没有真实改写能力：原样返回当前方案，保证"文字微调"流程可跑通
        return PlanResult.model_validate(current_plan)
