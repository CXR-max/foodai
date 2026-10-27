"""
约束求解方案生成器（PLAN_ENGINE=solver 时启用）

设计：大模型不参与方案构造，只保留识别/对话能力；方案由 CP-SAT 直接求解。
- 数据源：只用 app/data/foods_data.json（每 100g 营养 + 标准份量 common_portion_g）
- 变量：每餐每道菜 0~4 份（整数）；是否选该菜为 0/1 变量
- 硬约束：全天热量 ±7%；蛋白下限 1.2/1.6/1.8 g/kg（维持/减脂/增肌）；
          每餐 1~3 道菜；同一道菜一天只出现一次；午晚餐至少一道荤菜
- 软目标：热量偏离、餐次占比 28/38/28/6、口味命中、避免和前几天重复
- 限时：单次求解 0.8 秒；失败按 100%→85%→70%→55% 逐级放宽蛋白，仍失败抛 PlannerError
- 营养值：全部由 foods_data.calc_nutrition(food, 实际克重) 重算，不写死任何营养数字
"""
from __future__ import annotations

import logging
import math
from collections import defaultdict
from typing import Any

from ortools.sat.python import cp_model

from app.services import nutrition
from app.services.llm import foods_data
from app.services.llm.prompts import EXERCISE_METABOLIC
from app.services.llm.schemas import (
    PlanDay, PlanExercise, PlanMeal, PlanMealItem, PlanResult,
)

logger = logging.getLogger("planner")


class PlannerError(RuntimeError):
    """约束求解失败：已尝试全部蛋白放宽级别，仍无可行解。"""


# 餐次及目标热量占比（28/38/28/6）
MEALS: tuple[tuple[str, float], ...] = (
    ("breakfast", 0.28),
    ("lunch", 0.38),
    ("dinner", 0.28),
    ("snack", 0.06),
)

# 蛋白下限（g/kg）：维持 / 减脂 / 增肌
PROTEIN_FACTORS = {"maintain": 1.2, "lose": 1.6, "gain": 1.8}

# 求解失败时的逐级放宽系数（最低 70%，避免生成蛋白过低的“形式达标”方案）
PROTEIN_RELAX_LEVELS = (1.0, 0.90, 0.85, 0.70)

CALORIE_TOLERANCE = 0.07       # 全天热量允许偏离 ±7%
MAX_SERVINGS = 4               # 每餐每道菜最多 4 份（绝对上限）
MAX_GRAMS_PER_DISH = 500       # 单道菜最多 500g，避免 1000g 豆浆/800g 青菜
MIN_DISHES_PER_MEAL = 1
MAX_DISHES_PER_MEAL = 3
SOLVER_TIME_LIMIT = 0.8        # 单次求解限时（秒）

# CP-SAT 只能处理整数：统一放大 10 倍（热量 0.1 kcal，蛋白 0.1 g）
SCALE = 10

# 目标权重（同比例放大 10 倍；热量 10 : 餐次 1 的相对关系不变）
W_TOTAL_CAL = 10
W_MEAL_CAL = 3              # 餐次占比 28/38/28/6
W_TASTE = -4000             # 口味命中（每个匹配标签）
W_REPEAT_1 = 2000           # 与前一天重复
W_REPEAT_2 = 1200           # 与前两天重复
W_REPEAT_OLD = 800          # 与更早天数重复
W_EXTRA_SERVING = 6000      # 单菜超过 2 份：每多 1 份的惩罚

MEAL_COUNT = len(MEALS)
MAX_DISH_SLOTS_PER_DAY = MEAL_COUNT * MAX_DISHES_PER_MEAL

# 四舍五入安全余量：每道菜最多 0.1 单位误差，乘以最大份数
CAL_SAFETY_UNITS = MAX_DISH_SLOTS_PER_DAY * MAX_SERVINGS
PROTEIN_SAFETY_UNITS = MAX_DISH_SLOTS_PER_DAY // 2

THEMES = ("轻盈开局", "循序渐近", "渐入佳境", "稳步推进", "活力满格", "接近目标", "习惯养成")

# 前端口味选项 → 营养库 taste_tags 的别名映射（营养库只有清淡/偏咸/偏甜/偏辣/偏酸）
TASTE_ALIASES: dict[str, tuple[str, ...]] = {
    "咸鲜": ("偏咸",),
    "香辣": ("偏辣",),
    "微辣": ("偏辣",),
    "麻辣": ("偏辣",),
    "酸辣": ("偏辣", "偏酸"),
    "蒜香": ("偏咸",),
    "孜然": ("偏咸",),
    "酱香": ("偏咸",),
    "烧烤": ("偏咸",),
}

# 忌口/过敏关键词（按 foods_data.ingredients 匹配；标签只作为补充）
MEAT_KEYWORDS = (
    "肉", "猪", "排骨", "鸡翅", "鸡腿", "鸡爪", "鸡肉", "鸡胸肉",
    "牛肉", "牛腩", "牛排", "牛筋", "牛肚", "牛尾", "牛肝", "牛肉饼", "牛骨",
    "羊肉", "羊排", "鸭肉", "鸭腿", "鹅肉", "兔肉", "丸子",
    "火腿", "培根", "香肠",
)
SEAFOOD_KEYWORDS = (
    "鱼", "虾", "蟹", "贝", "蛤", "牡蛎", "扇贝", "鱿鱼", "章鱼",
    "海参", "海带", "紫菜", "虾皮", "鱼豆腐", "昆布",
)
SOY_KEYWORDS = (
    "大豆", "黄豆", "豆腐", "豆浆", "豆皮", "豆腐皮", "豆干", "腐竹",
    "毛豆", "豆豉", "豆奶", "酱油", "生抽", "老抽", "豆瓣酱", "味噌", "豆酱",
)
PEANUT_KEYWORDS = ("花生",)
NUT_KEYWORDS = (
    "花生", "核桃", "杏仁", "腰果", "榛子", "开心果", "松子", "夏威夷果", "碧根果",
)
DAIRY_KEYWORDS = (
    "牛奶", "奶酪", "芝士", "奶油", "黄油", "酸奶", "炼乳", "奶粉", "乳清", "鲜奶", "淡奶油",
)
GLUTEN_KEYWORDS = (
    "面粉", "面包", "面条", "馒头", "饺子", "包子", "油条", "饼", "披萨", "汉堡",
    "意大利面", "燕麦", "酱油", "生抽", "老抽", "豆瓣酱", "蚝油",
)
PORK_KEYWORDS = (
    "猪", "猪肉", "猪里脊", "五花肉", "排骨", "猪蹄", "猪肝", "猪腰",
    "火腿", "午餐肉", "培根", "香肠", "肉末", "肉丝", "肉丸", "肉饼", "肉馅", "瘦肉",
)
BEEF_KEYWORDS = ("牛肉", "牛腩", "牛排", "牛筋", "牛肚", "牛尾", "牛肝", "牛肉饼", "牛骨")
SPICY_KEYWORDS = ("辣椒", "干辣椒", "花椒", "豆瓣酱", "辣椒油", "剁椒", "泡椒")
SUGAR_KEYWORDS = ("糖", "冰糖", "白糖", "红糖", "蜂蜜", "可乐", "糖浆", "番茄酱", "果酱", "炼乳")
SALT_KEYWORDS = (
    "盐", "生抽", "酱油", "豆瓣酱", "蚝油", "蒸鱼豉油", "咸菜", "榨菜",
    "火腿", "午餐肉", "培根", "香肠", "蘸料",
)
OILY_KEYWORDS = ("炸", "油条", "薯条", "五花肉", "猪油", "黄油", "奶油", "食用油")
ALCOHOL_KEYWORDS = ("料酒", "啤酒", "白酒", "黄酒", "酒精")


# ==================== 档案解析 ====================

def _target_calories(profile_ctx: dict) -> int:
    """每日热量目标：优先 profile_ctx，缺省用现有营养服务的公式计算（不写死数字）"""
    raw = profile_ctx.get("daily_target_calories")
    if raw is None:
        raw = nutrition.calc_daily_target(
            profile_ctx.get("weight_kg"),
            profile_ctx.get("height_cm"),
            profile_ctx.get("age"),
            profile_ctx.get("gender"),
            profile_ctx.get("activity_level") or "moderate",
            profile_ctx.get("goal") or "maintain",
        )
    if not raw or float(raw) <= 0:
        raise PlannerError("缺少有效的每日热量目标（profile_ctx.daily_target_calories）")
    return int(round(float(raw)))


def _weight_kg(profile_ctx: dict) -> float:
    """当前体重（蛋白下限按当前体重计算）"""
    try:
        weight = float(profile_ctx.get("weight_kg"))
    except (TypeError, ValueError):
        weight = 0.0
    if weight <= 0:
        raise PlannerError("缺少有效体重（profile_ctx.weight_kg），无法计算蛋白下限")
    return weight


def _goal_factor(profile_ctx: dict) -> tuple[str, float]:
    goal = str(profile_ctx.get("goal") or "maintain").strip().lower()
    factor = PROTEIN_FACTORS.get(goal)
    if factor is None:
        logger.warning("未知 goal=%r，按 maintain 处理", goal)
        factor = PROTEIN_FACTORS["maintain"]
        goal = "maintain"
    return goal, factor


# ==================== 候选池 ====================

def _food_text(food: dict) -> str:
    return " ".join(str(item) for item in (food.get("ingredients") or []))


def _contains(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)


def _is_blocked(food: dict, restrictions: list[str]) -> bool:
    """按标签 + 食材关键词做禁忌剔除；未知忌口忽略。"""
    tags = set(food.get("taste_tags") or []) | set(food.get("dietary_tags") or [])
    text = _food_text(food)
    fat = float((food.get("per_100g") or {}).get("fat_g") or 0)

    for restriction in restrictions:
        if restriction in ("素食", "蛋奶素", "奶蛋素"):
            # 前端选项是“蛋奶素”；“奶蛋素”做兼容。素食=不食肉/海鲜，蛋奶可用
            if _contains(text, MEAT_KEYWORDS) or _contains(text, SEAFOOD_KEYWORDS):
                return True
        elif restriction == "大豆过敏":
            if "大豆" in tags or _contains(text, SOY_KEYWORDS):
                return True
        elif restriction == "花生过敏":
            if "含花生" in tags or _contains(text, PEANUT_KEYWORDS):
                return True
        elif restriction == "坚果过敏":
            if "含花生" in tags or _contains(text, NUT_KEYWORDS):
                return True
        elif restriction == "海鲜过敏":
            if "海鲜" in tags or _contains(text, SEAFOOD_KEYWORDS):
                return True
        elif restriction == "乳糖不耐":
            if "含乳" in tags or _contains(text, DAIRY_KEYWORDS):
                return True
        elif restriction == "麸质过敏":
            if _contains(text, GLUTEN_KEYWORDS):
                return True
        elif restriction == "忌辣":
            if "偏辣" in tags or _contains(text, SPICY_KEYWORDS):
                return True
        elif restriction == "低糖":
            if "偏甜" in tags or _contains(text, SUGAR_KEYWORDS):
                return True
        elif restriction == "低盐":
            if "偏咸" in tags or _contains(text, SALT_KEYWORDS):
                return True
        elif restriction == "少油":
            if fat > 20 or _contains(text, OILY_KEYWORDS):
                return True
        elif restriction in ("不吃猪肉", "清真"):
            if _contains(text, PORK_KEYWORDS):
                return True
            if restriction == "清真" and _contains(text, ALCOHOL_KEYWORDS):
                return True
        elif restriction == "不吃牛肉":
            if _contains(text, BEEF_KEYWORDS):
                return True
    return False


def _meal_pool(meal_type: str, restrictions: list[str]) -> list[dict]:
    """按餐次 + 禁忌过滤候选菜（禁忌在这里直接剔除，不进入求解模型）"""
    pool = [
        food for food in foods_data.load_foods()
        if meal_type in (food.get("suitable_meals") or [])
        and not _is_blocked(food, restrictions)
    ]
    if not pool:
        raise PlannerError(f"{meal_type} 在禁忌过滤后没有可选菜品")
    return pool


def _serving_units(food: dict) -> tuple[int, int]:
    """一份菜的热量/蛋白（0.1 单位），全部由 foods_data 按克重换算"""
    per = food["per_100g"]
    factor = float(food["common_portion_g"]) / 100.0
    calories = int(math.floor(float(per["calories"]) * factor * SCALE))
    protein = int(math.floor(float(per["protein_g"]) * factor * SCALE))
    return calories, protein


# ==================== 跨天去重 ====================

def _day_dish_names(day: Any) -> set[str]:
    """兼容 PlanDay 对象 / 路由层 collected 里的 dict"""
    if isinstance(day, dict):
        meals = day.get("meals") or []
    else:
        meals = getattr(day, "meals", None) or []
    names: set[str] = set()
    for meal in meals:
        # dict 也有 .items 方法，必须先判类型，不能直接 getattr(meal, "items")
        if isinstance(meal, dict):
            items = meal.get("items") or []
        else:
            items = getattr(meal, "items", None) or []
        for item in items:
            if isinstance(item, dict):
                name = item.get("name")
            else:
                name = getattr(item, "name", None)
            if name:
                names.add(str(name))
    return names


def _day_meal_names(day: Any, meal_type: str) -> set[str]:
    """取某一天某一餐的菜名，兼容 PlanDay / dict。"""
    if isinstance(day, dict):
        meals = day.get("meals") or []
    else:
        meals = getattr(day, "meals", None) or []
    for meal in meals:
        current_type = meal.get("meal_type") if isinstance(meal, dict) else getattr(meal, "meal_type", None)
        if current_type != meal_type:
            continue
        if isinstance(meal, dict):
            items = meal.get("items") or []
        else:
            items = getattr(meal, "items", None) or []
        names: set[str] = set()
        for item in items:
            name = item.get("name") if isinstance(item, dict) else getattr(item, "name", None)
            if name:
                names.add(str(name))
        return names
    return set()


# ==================== 单日求解 ====================

def _solve_day(
    pools: dict[str, list[dict]],
    taste_preferences: set[str],
    target: int,
    weight_kg: float,
    protein_factor: float,
    relax: float,
    prior_days: list[Any],
    vegetarian: bool,
) -> dict[str, list[tuple[dict, int]]] | None:
    """构造并求解单日模型；无可行解/超时返回 None，由调用方放宽蛋白后重试。"""
    model = cp_model.CpModel()

    x: dict[tuple[str, str], cp_model.IntVar] = {}
    y: dict[tuple[str, str], cp_model.BoolVar] = {}
    by_name: dict[str, list[cp_model.BoolVar]] = defaultdict(list)
    calories: dict[tuple[str, str], int] = {}
    protein: dict[tuple[str, str], int] = {}
    extra_servings: list[cp_model.IntVar] = []

    for meal_type, _ratio in MEALS:
        for food in pools[meal_type]:
            name = food["name"]
            key = (meal_type, name)
            portion_g = float(food["common_portion_g"])
            max_servings = min(MAX_SERVINGS, max(1, int(MAX_GRAMS_PER_DISH // portion_g)))
            x_var = model.NewIntVar(0, max_servings, f"x_{meal_type}_{len(x)}")
            y_var = model.NewBoolVar(f"y_{meal_type}_{len(y)}")
            x[key] = x_var
            y[key] = y_var
            # 0 份 = 不选；1~max_servings 份 = 选中
            model.Add(x_var <= max_servings * y_var)
            model.Add(x_var >= y_var)
            calories[key], protein[key] = _serving_units(food)
            by_name[name].append(y_var)
            # 单菜超过 2 份的部分单独计惩罚（仅对标准份量较小的菜生效）
            if max_servings > 2:
                extra = model.NewIntVar(0, max_servings - 2, f"extra_{meal_type}_{len(x)}")
                model.Add(extra >= x_var - 2)
                extra_servings.append(extra)

    total_cal = sum(calories[key] * x[key] for key in x)
    total_protein = sum(protein[key] * x[key] for key in x)

    target_units = target * SCALE
    lower = int(math.ceil(target_units * (1 - CALORIE_TOLERANCE)))
    upper = int(math.floor(target_units * (1 + CALORIE_TOLERANCE))) - CAL_SAFETY_UNITS
    model.Add(total_cal >= lower)
    model.Add(total_cal <= upper)

    required_protein = (
        int(math.ceil(weight_kg * protein_factor * relax * SCALE)) + PROTEIN_SAFETY_UNITS
    )
    model.Add(total_protein >= required_protein)

    # 每餐 1~3 道菜
    for meal_type, _ratio in MEALS:
        selected = [y[(meal_type, food["name"])] for food in pools[meal_type]]
        model.Add(sum(selected) >= MIN_DISHES_PER_MEAL)
        model.Add(sum(selected) <= MAX_DISHES_PER_MEAL)

    # 同一道菜一天只出现一次
    for vars_ in by_name.values():
        if len(vars_) > 1:
            model.Add(sum(vars_) <= 1)

    # 不允许和之前任何一天太像：每天至少换掉 2 道历史日的菜（只有 1 道菜时直接禁用）
    for prior_day in prior_days:
        prior_names = [name for name in _day_dish_names(prior_day) if name in by_name]
        if prior_names:
            required_different = min(2, len(prior_names))
            model.Add(
                sum(y for name in prior_names for y in by_name[name])
                <= len(prior_names) - required_different
            )

    # 最近两天的每一餐也必须至少换一道菜，避免连续几天同一餐完全一样
    for prior_day in prior_days[-2:]:
        for meal_type, _ratio in MEALS:
            prior_names = [
                name for name in _day_meal_names(prior_day, meal_type)
                if (meal_type, name) in y
            ]
            if prior_names:
                model.Add(
                    sum(y[(meal_type, name)] for name in prior_names) <= len(prior_names) - 1
                )

    # 午晚餐至少一道荤菜；素食用户跳过该约束（否则必然无解）
    if not vegetarian:
        for meal_type in ("lunch", "dinner"):
            meat = [
                y[(meal_type, food["name"])]
                for food in pools[meal_type]
                if food.get("category") == "荤菜"
            ]
            if not meat:
                raise PlannerError(f"{meal_type} 没有荤菜候选，无法满足“午晚餐至少一道荤菜”")
            model.Add(sum(meat) >= 1)

    # ---- 软目标 ----
    terms: list[Any] = []

    for extra in extra_servings:
        terms.append(W_EXTRA_SERVING * extra)

    total_dev = model.NewIntVar(0, target_units, "total_cal_dev")
    model.AddAbsEquality(total_dev, total_cal - target_units)
    terms.append(W_TOTAL_CAL * total_dev)

    for meal_type, ratio in MEALS:
        meal_cal = sum(
            calories[(meal_type, food["name"])] * x[(meal_type, food["name"])]
            for food in pools[meal_type]
        )
        meal_target = int(round(target_units * ratio))
        # 单餐热量没有硬上下限，域放宽到 2 倍目标，避免极端分配被变量域误判无解
        meal_dev = model.NewIntVar(0, target_units * 2, f"meal_dev_{meal_type}")
        model.AddAbsEquality(meal_dev, meal_cal - meal_target)
        terms.append(W_MEAL_CAL * meal_dev)

    if taste_preferences:
        taste_tags: set[str] = set()
        for preference in taste_preferences:
            taste_tags.update(TASTE_ALIASES.get(preference, (preference,)))
        if taste_tags:
            taste_score: Any = 0
            for meal_type, _ratio in MEALS:
                for food in pools[meal_type]:
                    hits = len(taste_tags & set(food.get("taste_tags") or []))
                    if hits:
                        taste_score += hits * y[(meal_type, food["name"])]
            terms.append(W_TASTE * taste_score)

    repeat_penalty: dict[str, int] = {}
    for age, prior_day in enumerate(reversed(prior_days), start=1):
        penalty = W_REPEAT_1 if age == 1 else (W_REPEAT_2 if age == 2 else W_REPEAT_OLD)
        for name in _day_dish_names(prior_day):
            repeat_penalty[name] = repeat_penalty.get(name, 0) + penalty
    if repeat_penalty:
        for name, vars_ in by_name.items():
            if name in repeat_penalty:
                terms.append(repeat_penalty[name] * sum(vars_))

    model.Minimize(sum(terms))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = SOLVER_TIME_LIMIT
    solver.parameters.num_search_workers = 8
    solver.parameters.random_seed = 0
    # 该模型上 CP-SAT 默认 presolve 会长时间停在 0 branch（9.15 实测），关掉后 0.2s 内可解
    solver.parameters.cp_model_presolve = False
    status = solver.Solve(model)

    if status == cp_model.MODEL_INVALID:
        raise PlannerError("CP-SAT 模型无效，请检查约束")
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None

    selected: dict[str, list[tuple[dict, int]]] = {}
    for meal_type, _ratio in MEALS:
        chosen = []
        for food in pools[meal_type]:
            servings = solver.Value(x[(meal_type, food["name"])])
            if servings > 0:
                chosen.append((food, servings))
        selected[meal_type] = chosen
    return selected


# ==================== 输出组装 ====================

def _build_exercise(profile_ctx: dict, day_index: int) -> PlanExercise:
    """运动安排复用原 mock 规则（不参与营养约束）"""
    preferences = [
        item for item in (profile_ctx.get("exercise_preferences") or [])
        if item in EXERCISE_METABOLIC
    ]
    exercise_type = (
        preferences[(day_index - 1) % len(preferences)]
        if preferences else ["快走", "瑜伽", "骑行"][day_index % 3]
    )
    duration = min(20 + 5 * (day_index - 1), 60)
    weight = float(profile_ctx.get("weight_kg") or 0)
    burned = int(EXERCISE_METABOLIC.get(exercise_type, 5) * duration * weight / 60) if weight > 0 else 0
    return PlanExercise(
        type=exercise_type,
        duration_min=duration,
        calories_burned=burned,
        tips="循序渐进，注意热身；当天感到疲劳可适当降低强度",
    )


def _to_plan_day(
    day_index: int,
    selected: dict[str, list[tuple[dict, int]]],
    profile_ctx: dict,
) -> PlanDay:
    meals: list[PlanMeal] = []
    for meal_type, _ratio in MEALS:
        items: list[PlanMealItem] = []
        calories = 0.0
        for food, servings in selected.get(meal_type, []):
            portion_g = float(food["common_portion_g"]) * servings
            nut = foods_data.calc_nutrition(food, portion_g)
            items.append(PlanMealItem(name=food["name"], portion_g=portion_g))
            calories += float(nut["calories"])
        if not items:
            raise PlannerError(f"第 {day_index} 天 {meal_type} 没有选出菜品")
        meals.append(PlanMeal(
            meal_type=meal_type,
            name="、".join(item.name for item in items),
            items=items,
            calories=round(calories, 1),
        ))
    return PlanDay(
        day_index=day_index,
        theme=THEMES[(day_index - 1) % len(THEMES)],
        meals=meals,
        exercise=_build_exercise(profile_ctx, day_index),
    )


# ==================== 对外接口 ====================

def build_day(
    profile_ctx: dict,
    day_index: int,
    days: int,
    prior_days: list[Any] | None = None,
    note: str = "",
    meta: dict | None = None,
) -> PlanDay:
    """构造方案中的某一天；供 generate_plan / generate_day / generate_day_stream 复用。"""
    if day_index < 1 or (days and day_index > days):
        raise PlannerError(f"非法 day_index={day_index}（days={days}）")

    target = _target_calories(profile_ctx)
    weight_kg = _weight_kg(profile_ctx)
    _goal, protein_factor = _goal_factor(profile_ctx)
    restrictions = list(profile_ctx.get("dietary_restrictions") or [])
    pools = {meal_type: _meal_pool(meal_type, restrictions) for meal_type, _ in MEALS}
    taste_preferences = set(profile_ctx.get("taste_preferences") or [])
    vegetarian = "素食" in restrictions or "蛋奶素" in restrictions or "奶蛋素" in restrictions
    prior_days = prior_days or []

    last_required = 0.0
    for relax in PROTEIN_RELAX_LEVELS:
        selected = _solve_day(
            pools=pools,
            taste_preferences=taste_preferences,
            target=target,
            weight_kg=weight_kg,
            protein_factor=protein_factor,
            relax=relax,
            prior_days=prior_days,
            vegetarian=vegetarian,
        )
        if selected is not None:
            if relax < 1.0:
                if meta is not None:
                    meta["relaxed"] = min(meta.get("relaxed", 1.0), relax)
                    meta["protein_factor"] = protein_factor
                logger.warning(
                    "第 %d 天蛋白下限放宽到 %.0f%%（%.2f g/kg）",
                    day_index, relax * 100, protein_factor * relax,
                )
            return _to_plan_day(day_index, selected, profile_ctx)
        last_required = weight_kg * protein_factor * relax
        logger.warning(
            "第 %d 天蛋白 %.1f g 在 %.1fs 内无解，继续放宽",
            day_index, last_required, SOLVER_TIME_LIMIT,
        )

    raise PlannerError(
        f"第 {day_index} 天在 {len(PROTEIN_RELAX_LEVELS)} 级蛋白放宽后仍无可行解"
        f"（目标 {target} kcal，体重 {weight_kg:g} kg，最低蛋白 {last_required:.1f} g）"
    )


def build_plan(
    profile_ctx: dict,
    days: int,
    note: str = "",
    meta: dict | None = None,
) -> PlanResult:
    """构造完整方案；任一天失败都会抛 PlannerError，绝不返回空方案。"""
    if days < 1:
        raise PlannerError("方案天数必须 >= 1")

    target = _target_calories(profile_ctx)
    _goal, protein_factor = _goal_factor(profile_ctx)
    track = meta if meta is not None else {}
    plan_days: list[PlanDay] = []
    for day_index in range(1, days + 1):
        plan_days.append(build_day(profile_ctx, day_index, days, plan_days, note, track))

    summary = (
        f"约束求解器已按每日 {target} kcal 目标生成 {days} 天方案"
        f"（蛋白质下限 {protein_factor:g} g/kg，热量允许 ±{CALORIE_TOLERANCE:.0%}）"
    )
    relaxed = track.get("relaxed", 1.0)
    if relaxed < 1.0:
        summary += f"；注意：当前忌口组合下蛋白下限已放宽到 {protein_factor * relaxed:.2f} g/kg"

    return PlanResult(
        summary=summary,
        daily_target_calories=target,
        days=plan_days,
    )
