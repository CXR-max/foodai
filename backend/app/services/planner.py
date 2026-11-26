"""
约束求解方案生成器（PLAN_ENGINE=solver 时启用）

设计：大模型不参与方案构造，只保留识别/对话能力；方案由 CP-SAT 直接求解。
- 数据源：只用 app/data/foods_data.json（每 100g 营养 + 标准份量 common_portion_g）
- 变量：每餐每道菜 0~4 份（整数）；是否选该菜为 0/1 变量
- 硬约束（七项，全部读 plan_targets 的区间，不自己算系数）：
  能量上下限、蛋白上下限、脂肪上下限、碳水上下限、
  纤维下限、钠上限、添加糖上限
- 其它硬约束：每餐 1~3 道菜；同一道菜一天只出现一次；午晚餐至少一道荤菜
- 降级链：纤维 → 钠 → 糖（结构项转软并留痕）；能量与蛋白/脂肪/碳水的上下限**永不降**
- 出餐后逐日复核（services/nutrition_check）：越界就重排，仍越界则拒绝出方案
- 软目标：热量偏离、餐次占比 28/38/28/6、口味命中、避免和前几天重复
- 限时：单次求解 0.8 秒
- 营养值：全部由 foods_data 按实际克重重算，不写死任何营养数字
"""
from __future__ import annotations

import logging
import math
from collections import defaultdict
from typing import Any

from ortools.sat.python import cp_model

from app.services import food_layers, nutrition, nutrition_check, plan_targets
from app.services.llm import foods_data
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

# 蛋白下限（g/kg）只用于展示说明；真正的约束一律读 plan_targets 给出的区间，
# 避免「生成用系数、判定用区间」两套数（这正是蛋白被顶到 133 g 的成因）

# 七项营养素：CP-SAT 只能处理整数，各自放大到 0.1 单位（钠按 1 mg）
NUTRIENT_SCALES: dict[str, int] = {
    "calories": 10, "protein_g": 10, "fat_g": 10, "carb_g": 10,
    "fiber_g": 10, "sodium_mg": 1, "sugar_g": 10,
}

# 取整安全余量（真实单位）：下限加、上限减，保证四舍五入后仍落在原区间内
SAFETY_REAL: dict[str, float] = {
    "calories": 10, "protein_g": 5, "fat_g": 3, "carb_g": 8,
    "fiber_g": 3, "sodium_mg": 100, "sugar_g": 3,
}

# 营养值键 → targets 里的区间键
BAND_KEYS: dict[str, tuple[str | None, str | None]] = {
    "calories": ("calorie_min", "calorie_max"),
    "protein_g": ("protein_min_g", "protein_max_g"),
    "fat_g": ("fat_min_g", "fat_max_g"),
    "carb_g": ("carb_min_g", "carb_max_g"),
    "fiber_g": ("fiber_min_g", None),
    "sodium_mg": (None, "sodium_max_mg"),
    "sugar_g": (None, "sugar_max_g"),
}

# 降级链：只降结构项，**能量与蛋白/脂肪/碳水的上下限永不降**
# 理由：这三项是「按方案吃完不许超」的承诺本体；纤维/钠/糖受菜库数据完整度限制
# （纤维 63.9%、糖 58.6% 覆盖，烹饪加盐根本不在菜库里），降级必须留痕给用户看
DEGRADE_LEVELS: tuple[tuple[str, ...], ...] = (
    (),
    ("fiber",),
    ("fiber", "sodium"),
    ("fiber", "sodium", "sugar"),
)

# 软化结构项的超量罚（钠每 mg、糖每 0.1 g）。软化只是「允许略超并留痕」，
# 罚必须有，否则解会落在 5500 mg 钠这种组合上
W_SOFT_OVER = {"sodium_mg": 1, "sugar_g": 10}

CALORIE_TOLERANCE = 0.07       # 没给区间时的兜底：目标 ±7%
MAX_SERVINGS = 4               # 每餐每道菜最多 4 份（绝对上限）
MAX_GRAMS_PER_DISH = 500       # 单道菜最多 500g，避免 1000g 豆浆/800g 青菜
MIN_DISHES_PER_MEAL = 1
MAX_DISHES_PER_MEAL = 3
# 加餐是小食组合（水果 + 酸奶 + 几颗坚果很正常），上限放宽到 4 道——
# 指南要求每天 12 种以上，正餐 3×3=9 种不够，缺额靠加餐补
MAX_DISHES_SNACK = 4
SOLVER_TIME_LIMIT = 3.0        # 常规单次求解限时（秒）：先快速试，超时不等于无解
# 融合主分支全量营养库（1850 条）后，CP-SAT 模型明显变大，3~8 s 内常返回
# UNKNOWN（没在时限内找到解）而不是 INFEASIBLE（数学上无解）。旧逻辑把两者
# 都当「无解」直接拒出方案，导致手机端 3 天 / 7 天方案都生成失败。
# 实测同一模型给到 20~30 s 即可求出 12 道菜的合规方案，故超时时加时重试。
SOLVER_TIMEOUT_RETRY_LIMIT = 20.0

# CP-SAT 只能处理整数：统一放大 10 倍（热量 0.1 kcal，蛋白 0.1 g）
SCALE = 10

# 目标权重（同比例放大 10 倍；热量 10 : 餐次 1 的相对关系不变）
W_TOTAL_CAL = 10
W_MEAL_CAL = 3              # 餐次占比 28/38/28/6
W_TASTE = -4000             # 口味命中（每个匹配标签）
W_INCLUDE_FOOD = -6000      # 对话里明确想吃的菜/食材
W_REPEAT_1 = 2000           # 与前一天重复
W_REPEAT_2 = 1200           # 与前两天重复
W_REPEAT_OLD = 800          # 与更早天数重复
W_EXTRA_SERVING = 6000      # 单菜超过 2 份：每多 1 份的惩罚
W_FIBER = 300               # 膳食纤维不足（每 0.1 g），营养库覆盖有限，只做低权重引导

# 宝塔分层偏差（每 0.1 g）。缺得越多罚得越多；吃过头罚得更重，防止「堆 1.4 kg 西兰花」凑数
W_LAYER_SHORT = 40
W_LAYER_OVER = 60

# 必须钉死的层：蔬菜吃不够、没有奶、没有动物性食物，这份方案就是不合格的
# 宝塔各层全部硬卡。曾经「七层全硬无解」，根因是当时蛋并入动物性层双重约束、
# 且脂肪没算烹调油——这两个修掉后实测全硬有解（2026-11-17，1821 kcal 档 7/7 天达标）
PAGODA_HARD_LAYERS = (
    food_layers.GRAIN, food_layers.VEG, food_layers.FRUIT,
    food_layers.ANIMAL, food_layers.EGG, food_layers.DAIRY, food_layers.BEAN,
)

MEAL_COUNT = len(MEALS)

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
    """当前体重（只用于报错文案；蛋白区间由 plan_targets 给）"""
    try:
        weight = float(profile_ctx.get("weight_kg"))
    except (TypeError, ValueError):
        weight = 0.0
    if weight <= 0:
        raise PlannerError("缺少有效体重（profile_ctx.weight_kg），无法计算蛋白区间")
    return weight


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


# 没有 suitable_meals 字段的食物，按宝塔层推断能上哪一餐
SLOT_BY_LAYER: dict[str, tuple[str, ...]] = {
    food_layers.GRAIN: ("breakfast", "lunch", "dinner"),
    food_layers.VEG: ("lunch", "dinner"),
    food_layers.FRUIT: ("breakfast", "snack"),
    food_layers.MEAT: ("lunch", "dinner"),
    food_layers.FISH: ("lunch", "dinner"),
    food_layers.EGG: ("breakfast", "lunch", "dinner"),
    food_layers.DAIRY: ("breakfast", "snack"),
    food_layers.BEAN: ("breakfast", "lunch", "dinner"),
    food_layers.OIL: (),
}
# 生食材里只有这些层可以直接端上桌（黄瓜、番茄、苹果、牛奶）；生肉生米不行
RAW_EDIBLE_LAYERS = (food_layers.VEG, food_layers.FRUIT, food_layers.DAIRY)

# 每餐候选上限：池子开太大 CP-SAT 变量数会爆，按层均衡取，保证每类都有得选
POOL_PER_LAYER = 40
POOL_LIMIT = 160


# 这些不是一道菜，是配料：池子放开后「生姜」「香菜」「干辣椒」会被当成菜排进餐，
# 既占掉每餐 1–3 道的名额，又让「跨天换菜」无解
SEASONING_WORDS = (
    "生姜", "老姜", "姜片", "姜丝", "大蒜", "蒜末", "蒜片", "蒜蓉酱", "葱花", "香菜",
    "花椒", "干辣椒", "辣椒粉", "八角", "桂皮", "香叶", "孜然", "胡椒", "料酒",
    "生抽", "老抽", "酱油", "陈醋", "香醋", "食盐", "味精", "鸡精", "淀粉", "生粉",
    "酵母", "小苏打", "泡打粉", "豆瓣酱", "甜面酱", "蚝油", "芝麻油", "香油",
)


def _is_seasoning(food: dict) -> bool:
    name = str(food.get("name") or "")
    layer = food_layers.layer_of(food)
    if layer in (food_layers.CONDIMENT, food_layers.DRINK, food_layers.SUGAR):
        # 调味料不是一道菜；饮料（可乐）和添加糖不该出现在任何方案里
        return True
    return any(word in name for word in SEASONING_WORDS)


# 数据库里这些条目不是一道菜：调味乳、强化乳、浓缩发酵乳、营养补充剂……
# 端给用户只会得到「这是啥」的反馈，直接挡在候选池外
POOL_BLOCK_WORDS = (
    "調味乳", "调味乳", "發酵乳", "发酵乳", "保久乳", "強化", "强化", "濃稠", "浓稠",
    "啤酒酵母", "寡糖", "寡醣", "飲料", "饮料", "補充", "补充", "粉末", "粉狀",
    "調理包", "调理包", "速食", "即食包", "營養", "营养",
)


def _is_usable(food: dict) -> bool:
    name = str(food.get("name") or "")
    return not any(word in name for word in POOL_BLOCK_WORDS)


def _slot_ok(food: dict, meal_type: str) -> bool:
    slots = food.get("suitable_meals") or []
    if slots:
        return meal_type in slots
    layer = food_layers.layer_of(food)
    allowed = SLOT_BY_LAYER.get(layer)
    if not allowed or meal_type not in allowed:
        return False
    state = str(food.get("state") or "")
    if state in ("cooked", "ready_to_eat", "processed"):
        return True
    # 生食材：只有本来就能生吃的层才允许直接进餐
    return layer in RAW_EDIBLE_LAYERS


# 菜名可读性排序：库里 1498 条来自台湾食药署，名字是「脫脂濃稠發酵乳(啤酒酵母)」
# 这种，端给用户没法看。自有种子和菜谱条目优先，其次才是数据库条目。
POOL_SOURCE_RANK = {"app_seed": 0, "recipe": 1, "legacy_json": 1}

# 高频繁体字：只放「繁体≠简体」的字。之前把 红/果/排/腿/丝 这类简体同形字
# 也混了进来，导致所有菜名都命中、繁体降权形同虚设——这是「中脂鮮羊乳」
# 仍会被排出来的根因。
TRADITIONAL_HINTS = set(
    "脫濃稠發調強鮮蝦雞鴨鵝鵪鶉鹹滷燉燜燴燙餘鳳蘋檸櫻棗獼甕麵蔥薑蘿蔔"
    "豬裡臟鱸鱈鯛鮭鮪鯖鰻蠔魷鮑欖霧芭樂龍糰餃餅湯餾餡餛飩麩"
    "臘燒魯粵臺灣肚腱腩腸筋肋胗腎腦酥鬆捲條絲漿醣"
    "營歲薺薢藍薯薴薹鰹鯊鰍鱔鰱鱅鯽鯉鱒饅饃餵餚髮髒膾膩"
)


def _has_traditional(name: str) -> bool:
    return any(char in TRADITIONAL_HINTS for char in name)


# 各层的「招牌菜」：层内排序时排在最前，保证奶/蛋/豆这几个容易被挤掉的层
# 出的是纯牛奶、鸡蛋、豆腐这种用户看得懂的菜，而不是保久羊乳
LAYER_FAVORITES: dict[str, tuple[str, ...]] = {
    food_layers.DAIRY: (
        "纯牛奶", "牛奶", "无糖酸奶", "酸奶", "全脂奶粉", "芝士", "木瓜牛奶",
    ),
    food_layers.EGG: (
        "鸡蛋", "水煮蛋", "茶叶蛋", "蒸水蛋", "蒸蛋", "荷包蛋(不加油)", "蛋饺",
    ),
    food_layers.BEAN: (
        "北豆腐", "甜豆浆", "豆腐干", "腐竹", "花生", "核桃", "腰果", "开心果", "毛豆",
    ),
    food_layers.FRUIT: (
        "苹果", "香蕉", "橙子", "西瓜", "葡萄", "蓝莓", "猕猴桃", "牛油果",
    ),
}


def _pool_rank(food: dict) -> tuple:
    """排序键（小者优先）。

    首键看来源：台湾食药署（tfda）1498 条的菜名是「中脂鮮羊乳」这种，一律排在
    自有种子 / 菜谱 / 遗留数据之后，只在前面不够时补位——靠 source 字段判别，
    比猜繁体字符可靠（早期用繁体字集合判定，漏字严重导致降权失效）。
    """
    source = str(food.get("source") or "")
    state = str(food.get("state") or "")
    name = str(food.get("name") or "")
    layer = food_layers.layer_of(food)
    favorites = LAYER_FAVORITES.get(layer) or ()
    fav_rank = next((i for i, fav in enumerate(favorites) if fav == name), len(favorites))
    return (
        fav_rank,
        1 if source == "tfda" else 0,
        0 if state in ("cooked", "ready_to_eat") else 1,
        -float(food.get("common_portion_g") or 0),
    )


def _trim_pool(pool: list[dict]) -> list[dict]:
    """按宝塔层均衡截断：每层留一批，避免某一大类挤掉别的层"""
    by_layer: dict[str, list[dict]] = defaultdict(list)
    keep = {food_layers.MEAT: POOL_PER_LAYER, food_layers.FISH: POOL_PER_LAYER,
            food_layers.EGG: 30, food_layers.DAIRY: 30,
            food_layers.FRUIT: 40, food_layers.BEAN: 40,
            food_layers.GRAIN: 40, food_layers.VEG: 50}
    others: list[dict] = []
    for food in pool:
        layer = food_layers.layer_of(food)
        if layer in keep:
            by_layer[layer].append(food)
        else:
            others.append(food)
    picked: list[dict] = []
    for layer, items in by_layer.items():
        items.sort(key=_pool_rank)
        picked.extend(items[:keep.get(layer, POOL_PER_LAYER)])
    others.sort(key=_pool_rank)
    picked.extend(others[:max(0, POOL_LIMIT - len(picked))])
    if not picked:
        picked = pool
    return picked


def _meal_pool(
    meal_type: str,
    restrictions: list[str],
    exclude_foods: list[str] | None = None,
) -> list[dict]:
    """按餐次 + 禁忌 + 对话里明确不吃的关键词过滤候选菜。

    以前只认 suitable_meals 字段，而 1847 条食物里 1720 条根本没这个字段，
    等于候选池只有 127 条——方案天天西兰花、鸡胸肉、米饭，重复到没法吃。
    现在没有该字段时按宝塔层推断餐次，可食状态不对（生米、生肉）才剔除。
    """
    exclude = [str(item).strip() for item in (exclude_foods or []) if str(item).strip()]
    pool: list[dict] = []
    for food in foods_data.load_foods():
        if _is_seasoning(food) or not _is_usable(food):
            continue
        if not _slot_ok(food, meal_type):
            continue
        if exclude:
            text = f"{food.get('name') or ''} {_food_text(food)}"
            if any(keyword in text for keyword in exclude):
                continue
        if _is_blocked(food, restrictions):
            continue
        pool.append(food)
    if not pool:
        raise PlannerError(f"{meal_type} 在禁忌过滤后没有可选菜品")
    return _trim_pool(pool)


# 降级链里用的短名
_SOFT_NAME = {"fiber_g": "fiber", "sodium_mg": "sodium", "sugar_g": "sugar"}


def _num(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


COOKING_OIL_G = 25   # 膳食指南：全日烹调用油 25–30 g
SALT_G = 5

# 全日烹调油的营养贡献（25 g 油 = 225 kcal、25 g 脂肪）
# 为什么必须单独计：食物库里的菜只写食材本身，不含烹饪用油，而七项目标
# （脂肪下限 20% 供能）是按「含油」算的。不把这一项加进去，脂肪永远差 25 g，
# 求解器就只能靠水煮鱼、红烧肉这类高油菜去补——「菜必须重口」是被数据逼出来的。
OIL_NUTRIENTS: dict[str, float] = {
    "calories": COOKING_OIL_G * 9.0,
    "fat_g": COOKING_OIL_G,
    "protein_g": 0.0, "carb_g": 0.0, "fiber_g": 0.0, "sodium_mg": 0.0, "sugar_g": 0.0,
}


def _day_nutrients(selected: dict[str, list[tuple[dict, int]]]) -> dict:
    """按实际克重重算一天的七项营养（与展示侧同一套换算，含全日烹调油）"""
    totals = {key: 0.0 for key in NUTRIENT_SCALES}
    for meal_type, _ratio in MEALS:
        for food, servings in selected.get(meal_type, []):
            portion_g = float(food["common_portion_g"]) * servings
            values = foods_data.calc_diet_nutrients(food, portion_g)
            for key, value in values.items():
                totals[key] += float(value or 0)
    for key, value in OIL_NUTRIENTS.items():
        totals[key] += value
    return {key: round(value, 1) for key, value in totals.items()}


def _verify(nutrients: dict, targets: dict, soft_keys: tuple[str, ...]) -> dict:
    """生成侧自检：按方案吃完，七项是否都落在区间内"""
    soft = frozenset(
        name for nutrient, name in _SOFT_NAME.items() if name in soft_keys
    )
    return nutrition_check.check(nutrients, targets, soft_keys=soft)


def _serving_units(food: dict) -> dict[str, int]:
    """一份菜的七项营养（各自按 NUTRIENT_SCALES 取整），全部由 foods_data 按克重换算

    纤维/钠/糖缺失时按同类中位数补（见 foods_data.reference_per_100g）：
    按 0 算会系统性低估，方案会「看起来纤维达标、实际只有一半」。
    """
    per = food["per_100g"]
    factor = float(food["common_portion_g"]) / 100.0
    units: dict[str, int] = {}
    for key, scale in NUTRIENT_SCALES.items():
        raw = per.get(key)
        if raw is None:
            raw = foods_data.reference_per_100g(food, key)
        units[key] = int(math.floor(float(raw or 0) * factor * scale))
    return units


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

def _layer_penalty_terms(model, x, pools: dict[str, list[dict]], target: float,
                         hard_layers: tuple[str, ...] | None = None) -> list:
    """宝塔各层：核心层用硬约束，其余用偏差惩罚（单位 0.1 g）。

    为什么不全硬：宝塔本身（25 g 油 + 300 g 奶 + 每天一个蛋 + 豆）的脂肪就在
    55–65 g，而减脂目标的脂肪上限只有 61 g，七层全硬会直接判无解。
    所以只把「吃不到就明显不均衡」的几层钉死，其余按权重尽量靠。

    权重口径：差 100 g 的惩罚约等于 400 kcal 的热量偏差，压在热量软目标之上。
    """
    hard_layers = tuple(hard_layers) if hard_layers is not None else PAGODA_HARD_LAYERS
    layer_units = _layer_units(pools)
    terms: list = []
    for layer, (low, high) in food_layers.pagoda_range(target).items():
        if layer == food_layers.ANIMAL:
            units: dict[tuple[str, str], int] = {}
            for sub in food_layers.ANIMAL_LAYERS:
                units.update(layer_units.get(sub) or {})
        else:
            units = layer_units.get(layer) or {}
        if not units:
            continue
        expr = sum(units[key] * x[key] for key in units)
        low_u = int(math.ceil(low * SCALE))
        high_u = int(math.floor(high * SCALE))

        if layer in hard_layers:
            # 只硬卡下限：上限走 over 罚。「层上限 + 热量上限 + 种类下限」三硬叠加
            # 实测无解（份量粒度太粗，层上限容易互相挤爆），下限才是不均衡的根
            model.Add(expr >= low_u)
            over = model.NewIntVar(0, int(500 * SCALE), f"over_{layer}")
            model.Add(over >= expr - high_u)
            terms.append(W_LAYER_OVER * over)
            continue

        short = model.NewIntVar(0, max(1, low_u), f"short_{layer}")
        model.Add(short >= low_u - expr)
        over = model.NewIntVar(0, int(500 * SCALE), f"over_{layer}")
        model.Add(over >= expr - high_u)
        terms.append(W_LAYER_SHORT * short)
        terms.append(W_LAYER_OVER * over)
    return terms


def _layer_units(pools: dict[str, list[dict]]) -> dict[str, dict[tuple[str, str], int]]:
    """每道菜「一份」折算到宝塔层的克重（按 0.1 g 取整，供 CP-SAT 用）"""
    index = food_layers.index()
    out: dict[str, dict[tuple[str, str], int]] = {}
    for meal_type, _ratio in MEALS:
        for food in pools[meal_type]:
            layer = food_layers.layer_of(food)
            if layer not in food_layers.PAGODA_POINT and layer not in food_layers.ANIMAL_LAYERS:
                continue
            grams, _basis = index.to_pagoda_weight(
                str(food.get("name") or ""), float(food.get("common_portion_g") or 0),
            )
            grams *= food_layers.main_layer_share(food)
            if grams <= 0:
                continue
            out.setdefault(layer, {})[(meal_type, food["name"])] = int(round(grams * SCALE))
    return out


def _solve_day(
    pools: dict[str, list[dict]],
    taste_preferences: set[str],
    target: int,
    targets: dict,
    prior_days: list[Any],
    include_foods: set[str],
    enforce_include: bool,
    relax_variety: bool,
    vegetarian: bool,
    soft_keys: tuple[str, ...] = (),
    pagoda_hard: tuple[str, ...] | None = None,
    species_min: int | None = None,
    time_limit: float | None = None,
) -> tuple[dict[str, list[tuple[dict, int]]] | None, int]:
    """构造并求解单日模型；返回 (选菜结果, CP-SAT status)。

    无可行解/超时时结果为 None，由调用方按 status 区分「真无解」与「超时」后处理。

    七项营养约束全部读 targets：
    - 能量 / 蛋白 / 脂肪 / 碳水：上下限都硬（吃完不许超）
    - 纤维（下限）、钠 / 糖（上限）：默认硬，soft_keys 里列出的转软并留痕
    - pagoda_hard / species_min：宝塔硬约束层与种类下限；降级到最后一级时
      放宽（保交付优先于保全达标）
    """
    model = cp_model.CpModel()

    x: dict[tuple[str, str], cp_model.IntVar] = {}
    y: dict[tuple[str, str], cp_model.BoolVar] = {}
    by_name: dict[str, list[cp_model.BoolVar]] = defaultdict(list)
    serving: dict[str, dict[tuple[str, str], int]] = {key: {} for key in NUTRIENT_SCALES}
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
            units = _serving_units(food)
            for nutrient, value in units.items():
                serving[nutrient][key] = value
            by_name[name].append(y_var)
            # 单菜超过 2 份的部分单独计惩罚（仅对标准份量较小的菜生效）
            if max_servings > 2:
                extra = model.NewIntVar(0, max_servings - 2, f"extra_{meal_type}_{len(x)}")
                model.Add(extra >= x_var - 2)
                extra_servings.append(extra)

    total = {
        nutrient: sum(serving[nutrient][key] * x[key] for key in x)
        + int(round(OIL_NUTRIENTS.get(nutrient, 0.0) * NUTRIENT_SCALES[nutrient]))
        for nutrient in NUTRIENT_SCALES
    }

    # ---- 七项营养硬约束 ----
    target_units = target * SCALE
    soft_over_terms: list = []
    for nutrient, (min_key, max_key) in BAND_KEYS.items():
        scale = NUTRIENT_SCALES[nutrient]
        soft = _SOFT_NAME.get(nutrient) in soft_keys
        safety = int(round(SAFETY_REAL[nutrient] * scale))

        if nutrient == "calories" and not (min_key and _num(targets.get(min_key))):
            # 没有区间时退回旧口径：目标 ±7%
            model.Add(total[nutrient] >= int(math.ceil(target_units * (1 - CALORIE_TOLERANCE))))
            model.Add(total[nutrient] <= int(math.floor(target_units * (1 + CALORIE_TOLERANCE))) - safety)
            continue

        if soft:
            # 软化 ≠ 放飞：给「2 倍放宽的硬上限 + 贴近原区间的罚」。
            # 只有罚时解会落在 5500 mg 钠/110 g 糖这种组合上（时限内优化不动）；
            # 全硬又与宝塔下限冲突，2 倍硬帽是「允许略超但绝不放飞」的折中
            low_soft = _num(targets.get(min_key)) if min_key else None
            high_soft = _num(targets.get(max_key)) if max_key else None
            if low_soft:
                floor_u = int(math.ceil(low_soft * scale * 0.5)) + safety
                model.Add(total[nutrient] >= floor_u)
            if high_soft:
                cap_u = int(math.floor(high_soft * scale * 2.0)) - safety
                model.Add(total[nutrient] <= cap_u)
                aim_u = int(math.floor(high_soft * scale)) - safety
                over_u = model.NewIntVar(0, max(1, cap_u - aim_u), f"soft_over_{nutrient}")
                model.Add(over_u >= total[nutrient] - aim_u)
                soft_over_terms.append(W_SOFT_OVER[nutrient] * over_u)
            continue
        low = _num(targets.get(min_key)) if min_key else None
        high = _num(targets.get(max_key)) if max_key else None
        if low is not None:
            model.Add(total[nutrient] >= int(math.ceil(low * scale)) + safety)
        if high is not None:
            model.Add(total[nutrient] <= int(math.floor(high * scale)) - safety)

    # 转软的结构项：纤维给低权重惩罚（别排出一份只有 8 g 纤维的菜），钠/糖不做惩罚
    fiber_short: cp_model.IntVar | None = None
    if "fiber" in soft_keys:
        required_fiber = int(math.ceil(float(_num(targets.get("fiber_min_g")) or 0) * SCALE))
        if required_fiber > 0:
            fiber_short = model.NewIntVar(0, required_fiber, "fiber_short")
            model.Add(total["fiber_g"] + fiber_short >= required_fiber)
            fiber_short_weight = W_FIBER
    else:
        fiber_short_weight = W_FIBER

    # 每餐 1~3 道菜（加餐最多 4 道）
    for meal_type, _ratio in MEALS:
        selected = [y[(meal_type, food["name"])] for food in pools[meal_type]]
        max_dishes = MAX_DISHES_SNACK if meal_type == "snack" else MAX_DISHES_PER_MEAL
        model.Add(sum(selected) >= MIN_DISHES_PER_MEAL)
        model.Add(sum(selected) <= max_dishes)

    # 同一道菜一天只出现一次
    for vars_ in by_name.values():
        if len(vars_) > 1:
            model.Add(sum(vars_) <= 1)

    # 同一道菜不连续 3 天：前两天都出现的菜今天禁用（糖醋萝卜连吃 6 天就是这么来的）。
    # 全禁会把可行域卡死（第 3 天起无解实测），只禁重复罚最高的前 2 道，其余靠 W_REPEAT 软罚。
    # relax_variety（跨天约束已确认过紧的降级路径）时连硬禁也放开
    repeat_penalty: dict[str, int] = {}
    for age, prior_day in enumerate(reversed(prior_days), start=1):
        penalty = W_REPEAT_1 if age == 1 else (W_REPEAT_2 if age == 2 else W_REPEAT_OLD)
        for name in _day_dish_names(prior_day):
            repeat_penalty[name] = repeat_penalty.get(name, 0) + penalty
    if len(prior_days) >= 2 and not relax_variety:
        last_two = [_day_dish_names(prior_days[-1]), _day_dish_names(prior_days[-2])]
        banned = set(last_two[0]) & set(last_two[1]) & set(by_name)
        if banned:
            hard_ban = sorted(banned, key=lambda n: -repeat_penalty.get(n, 0))[:2]
            if len(hard_ban) <= len(by_name) // 4:
                for name in hard_ban:
                    model.Add(sum(by_name[name]) == 0)

    # ---- 食物多样性：每天 ≥12 种（指南「平均每天 12 种以上」）----
    # 按「归一名」去重计数：米饭/大米粥同源但属两种食物没问题，蒸红薯/红薯(熟)是同一种
    species_of: dict[str, list] = defaultdict(list)
    for (meal_type, name), var in y.items():
        species_of[food_layers.canonical_name(name)].append(var)
    species_vars: dict[str, Any] = {}
    for canon, vars_ in species_of.items():
        z = model.NewBoolVar(f"species_{canon}")
        model.AddMaxEquality(z, vars_)
        species_vars[canon] = z
    model.Add(sum(species_vars.values()) >= (species_min or food_layers.SPECIES_PER_DAY_MIN))

    # ---- 宝塔分层（膳食均衡的本体）----
    # 只有热量和七项营养时，求解器会用「大碗米饭 + 大块肉」凑数：蔬菜只剩西兰花、
    # 奶和豆一口没有。这里按《中国居民膳食指南(2022)》的每日克重给每层定偏差。
    #
    # 为什么是软目标而不是硬约束：宝塔本身（25 g 油 + 300 g 奶 + 每天一个蛋 + 豆）
    # 的脂肪就到 55–65 g，而减脂目标的脂肪上限只有 61 g，两项硬卡会直接判无解。
    # 承诺仍然留在该硬的地方（热量、蛋白/脂肪/碳水上下限），宝塔按权重尽量满足，
    # 没满足的层在 day_flags 里留痕，详情页如实告诉用户「今天蔬菜差 80 g」。
    layer_penalty = _layer_penalty_terms(model, x, pools, target, hard_layers=pagoda_hard)

    # 对话里明确想吃的菜/食材：第一天至少安排一道（若当前忌口下存在可选菜）；
    # 后续天仍保留强软偏好，避免每天都强行同一道菜。
    if include_foods and enforce_include:
        include_vars = []
        for meal_type, _ratio in MEALS:
            for food in pools[meal_type]:
                text = f"{food.get('name') or ''} {_food_text(food)}"
                if any(keyword in text for keyword in include_foods):
                    include_vars.append(y[(meal_type, food["name"])])
        if include_vars:
            model.Add(sum(include_vars) >= 1)

    # 不允许和之前任何一天太像：每天至少换掉 2 道历史日的菜。更狠的「换 1/3」
    # 与「不连续 3 天」「宝塔硬下限」三者叠加会直接无解；防重复的主力是下面的
    # 禁连 3 天硬禁用 + W_REPEAT 罚，这条只兜底（只有 1 道菜时直接禁用）
    for prior_day in ([] if relax_variety else prior_days):
        prior_names = [name for name in _day_dish_names(prior_day) if name in by_name]
        if prior_names:
            required_different = min(2, len(prior_names))
            # 候选菜总数都不够换 2 道时，硬约束必然无解（极窄忌口池）
            if len(by_name) <= required_different:
                continue
            model.Add(
                sum(y for name in prior_names for y in by_name[name])
                <= len(prior_names) - required_different
            )

    # 最近两天的每一餐也必须至少换一道菜，避免连续几天同一餐完全一样
    for prior_day in ([] if relax_variety else prior_days[-2:]):
        for meal_type, _ratio in MEALS:
            available = [
                food["name"] for food in pools[meal_type]
                if (meal_type, food["name"]) in y
            ]
            # 该餐只有 1 个候选（或上一餐已占满全部候选）时，无法再换菜；
            # 跳过这条硬约束，否则加餐只有纯牛奶时第 2 天必无解。
            if len(available) <= 1:
                continue
            prior_names = [
                name for name in _day_meal_names(prior_day, meal_type)
                if (meal_type, name) in y
            ]
            if not prior_names:
                continue
            if set(prior_names) >= set(available):
                continue
            model.Add(
                sum(y[(meal_type, name)] for name in prior_names) <= len(prior_names) - 1
            )

    # 午晚餐至少一道荤菜；素食用户跳过。
    # 窄忌口池里可能只剩 1 道荤菜、而同一道菜一天只能出现一次；此时退化为
    # “午/晚至少一顿有荤菜”，避免第 1 天就无解。
    if not vegetarian:
        meat_by_meal: dict[str, list] = {}
        meat_names: set[str] = set()
        for meal_type in ("lunch", "dinner"):
            meat_by_meal[meal_type] = []
            for food in pools[meal_type]:
                if food.get("category") == "荤菜":
                    meat_by_meal[meal_type].append(y[(meal_type, food["name"])])
                    meat_names.add(food["name"])

        available_meals = [m for m in ("lunch", "dinner") if meat_by_meal[m]]
        if len(available_meals) == 2 and len(meat_names) >= 2:
            for meal_type in available_meals:
                model.Add(sum(meat_by_meal[meal_type]) >= 1)
        elif available_meals:
            model.Add(sum(v for meal_type in available_meals for v in meat_by_meal[meal_type]) >= 1)

    # ---- 软目标 ----
    terms: list[Any] = list(layer_penalty) + soft_over_terms

    for extra in extra_servings:
        terms.append(W_EXTRA_SERVING * extra)

    if fiber_short is not None:
        terms.append(fiber_short_weight * fiber_short)

    total_dev = model.NewIntVar(0, target_units, "total_cal_dev")
    model.AddAbsEquality(total_dev, total["calories"] - target_units)
    terms.append(W_TOTAL_CAL * total_dev)

    for meal_type, ratio in MEALS:
        meal_cal = sum(
            serving["calories"][(meal_type, food["name"])] * x[(meal_type, food["name"])]
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

    if include_foods:
        include_score: Any = 0
        for meal_type, _ratio in MEALS:
            for food in pools[meal_type]:
                text = f"{food.get('name') or ''} {_food_text(food)}"
                hits = sum(1 for keyword in include_foods if keyword and keyword in text)
                if hits:
                    include_score += hits * y[(meal_type, food["name"])]
        terms.append(W_INCLUDE_FOOD * include_score)
    if repeat_penalty:
        for name, vars_ in by_name.items():
            if name in repeat_penalty:
                terms.append(repeat_penalty[name] * sum(vars_))

    model.Minimize(sum(terms))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit if time_limit else SOLVER_TIME_LIMIT
    solver.parameters.num_search_workers = 8
    solver.parameters.random_seed = 0
    # presolve 曾在旧模型上卡 0 branch（9.15 实测），扩池+宝塔硬约束的新模型上
    # 实测开启后收敛更快，保持开启
    solver.parameters.cp_model_presolve = True
    status = solver.Solve(model)

    if status == cp_model.MODEL_INVALID:
        raise PlannerError("CP-SAT 模型无效，请检查约束")
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None, status

    selected: dict[str, list[tuple[dict, int]]] = {}
    for meal_type, _ratio in MEALS:
        chosen = []
        for food in pools[meal_type]:
            servings = solver.Value(x[(meal_type, food["name"])])
            if servings > 0:
                chosen.append((food, servings))
        selected[meal_type] = chosen
    return selected, status


# ==================== 输出组装 ====================

def _to_plan_day(
    day_index: int,
    selected: dict[str, list[tuple[dict, int]]],
    profile_ctx: dict,
    targets: dict,
    day_flags: dict | None = None,
) -> PlanDay:
    meals: list[PlanMeal] = []
    for meal_type, _ratio in MEALS:
        items: list[PlanMealItem] = []
        calories = 0.0
        for food, servings in selected.get(meal_type, []):
            portion_g = float(food["common_portion_g"]) * servings
            nut = foods_data.calc_nutrition(food, portion_g)
            items.append(PlanMealItem(
                name=food["name"], portion_g=portion_g,
                ingredients=food.get("ingredients") or [],
            ))
            calories += float(nut["calories"])
        if not items:
            raise PlannerError(f"第 {day_index} 天 {meal_type} 没有选出菜品")
        meals.append(PlanMeal(
            meal_type=meal_type,
            name="、".join(item.name for item in items),
            items=items,
            calories=round(calories, 1),
        ))
    # 运动处方 / 睡眠方案 / 当日目标：本地按目标体系算，不依赖模型
    extra = plan_targets.enrich_day(profile_ctx, targets, day_index)

    # 宝塔五层的实际克重与达标情况：详情页据此显示「今天蔬菜差 80 g、奶没喝够」
    flags = dict(day_flags or {})
    try:
        profile = food_layers.day_profile([m.model_dump() for m in meals])
        flags["pagoda"] = {
            "layers": {
                key: {"name": value["name"], "raw_g": value["raw_g"],
                      "min": value["min"], "max": value["max"], "status": value["status"]}
                for key, value in profile["layers"].items()
                if value["min"] is not None
            },
            "species": profile["species"],
            "species_ok": profile["species_ok"],
            "oil_g": food_layers.COOKING_OIL_G,
        }
    except Exception:  # noqa: BLE001 —— 体检失败不影响出方案
        logger.exception("第 %s 天宝塔体检失败", day_index)

    return PlanDay(
        day_index=day_index,
        theme=THEMES[(day_index - 1) % len(THEMES)],
        meals=meals,
        exercise=PlanExercise(**extra["exercise"]) if extra["exercise"] else None,
        exercises=[PlanExercise(**item) for item in extra["exercises"]],
        sleep=extra["sleep"],
        targets=extra["targets"],
        water_ml=extra["water_ml"],
        nutrition=_day_nutrients(selected),
        day_flags=flags or None,
    )


# ==================== 对外接口 ====================

def build_day(
    profile_ctx: dict,
    day_index: int,
    days: int,
    prior_days: list[Any] | None = None,
    note: str = "",
    meta: dict | None = None,
    targets_override: dict | None = None,
) -> PlanDay:
    """构造方案中的某一天；供 generate_plan / generate_day / generate_day_stream 复用。

    targets_override：处置下调了某几天的热量区间后重排用。**只覆盖传进来的键**
    （通常只有 calorie_min / calorie_max），蛋白等七项仍按档案算——处置从不放宽营养。
    不传就完全是原行为。
    """
    if day_index < 1 or (days and day_index > days):
        raise PlannerError(f"非法 day_index={day_index}（days={days}）")

    targets = {**plan_targets.build_targets(profile_ctx), **(targets_override or {})}
    if targets_override:
        # 软目标必须跟着新区间走，否则求解器还在往原热量靠，下调等于白做
        target = int(round((float(targets.get("calorie_min") or 0)
                            + float(targets.get("calorie_max") or 0)) / 2))
    else:
        target = _target_calories(profile_ctx)
    restrictions = list(profile_ctx.get("dietary_restrictions") or [])
    exclude_foods = [
        str(item).strip() for item in (profile_ctx.get("exclude_foods") or [])
        if str(item).strip()
    ]
    include_foods = {
        str(item).strip() for item in (profile_ctx.get("include_foods") or [])
        if str(item).strip()
    }
    pools = {
        meal_type: _meal_pool(meal_type, restrictions, exclude_foods)
        for meal_type, _ in MEALS
    }
    taste_preferences = set(profile_ctx.get("taste_preferences") or [])
    vegetarian = "素食" in restrictions or "蛋奶素" in restrictions or "奶蛋素" in restrictions
    prior_days = prior_days or []

    # 降级顺序：先保证营养（七项全硬），营养满足不了才动多样性，最后才动结构项。
    # 最后一级放宽除蛋/豆外的宝塔硬层（蛋和豆份量小、最容易被挤没，保持硬卡），
    # 种类下限降到 10：宁可「差一点全达标」留痕降级，也不直接拒出方案
    last_error = ""
    timeout_retries_left = 2     # 跨天约束累积后前几级可能连着超时，留两次加时机会
    for soft_keys in DEGRADE_LEVELS:
        last_resort = soft_keys == DEGRADE_LEVELS[-1]
        pagoda_hard = None
        species_min = None
        time_limit = SOLVER_TIME_LIMIT
        if last_resort:
            pagoda_hard = tuple(layer for layer in PAGODA_HARD_LAYERS
                                if layer not in (food_layers.EGG, food_layers.BEAN))
            species_min = 10
            # 兜底一级直接用长时限：跨天不重复约束叠加后，第 3 天起 8s 也解不完，
            # 而这一级往往是唯一能出解的一级，不能让它再被短时限误判。
            time_limit = max(SOLVER_TIME_LIMIT, SOLVER_TIMEOUT_RETRY_LIMIT)
        for variety_relaxed in (False, True):
            solve_kwargs = dict(
                pools=pools,
                taste_preferences=taste_preferences,
                target=target,
                targets=targets,
                prior_days=prior_days,
                include_foods=include_foods,
                enforce_include=(day_index == 1 and not variety_relaxed),
                relax_variety=variety_relaxed,
                vegetarian=vegetarian,
                soft_keys=soft_keys,
                pagoda_hard=pagoda_hard,
                species_min=species_min,
                time_limit=time_limit,
            )
            selected, status = _solve_day(**solve_kwargs)
            if (selected is None and status == cp_model.UNKNOWN
                    and timeout_retries_left > 0):
                # 超时 ≠ 无解：全量营养库模型较大，给当前约束一次更长的求解窗口，
                # 免得降级链全跑完仍被短时限误判成「菜品不足」而拒出方案。
                timeout_retries_left -= 1
                logger.warning(
                    "第 %d 天 %.1fs 求解超时（UNKNOWN），加时 %.1fs 重试",
                    day_index, time_limit, SOLVER_TIMEOUT_RETRY_LIMIT)
                solve_kwargs["time_limit"] = SOLVER_TIMEOUT_RETRY_LIMIT
                selected, status = _solve_day(**solve_kwargs)
            if selected is None:
                reason = "求解超时" if status == cp_model.UNKNOWN else "无可行解"
                last_error = (f"七项营养约束在 {solve_kwargs['time_limit']}s 内"
                              f"未找到可行解（{reason}，降级 {soft_keys or '无'}）")
                continue

            nutrients = _day_nutrients(selected)
            verdict = _verify(nutrients, targets, soft_keys)
            if not verdict["ok"]:
                # 求解器按整数单位挑菜，重算后仍可能因取整越界：宁可重排，不带病交付
                last_error = "；".join(
                    nutrition_check.violation_text(item) for item in verdict["violations"]
                )
                logger.warning(
                    "第 %d 天复核不过（%s），继续降级重排", day_index, last_error,
                )
                continue

            flags: dict[str, Any] = {"verified": True, "soft": list(soft_keys)}
            if variety_relaxed:
                flags["variety_relaxed"] = True
                logger.warning("第 %d 天跨天不重复约束过紧，已放宽以返回可行方案", day_index)
            if last_resort:
                flags["pagoda_relaxed"] = True
                logger.warning("第 %d 天宝塔硬约束过紧，已降为核心三层 + 种类≥10", day_index)
            if soft_keys:
                flags["relaxed"] = [
                    {
                        "key": item["key"], "label": item["label"],
                        "value": item["value"], "range_text": item["range_text"],
                    }
                    for item in verdict["relaxed"]
                ]
                logger.warning("第 %d 天结构项降级：%s", day_index, "、".join(soft_keys))
            if verdict["unverified"]:
                flags["unverified"] = verdict["unverified"]
            if meta is not None:
                for key in ("soft", "variety_relaxed"):
                    if flags.get(key):
                        meta[key] = flags[key]
            return _to_plan_day(day_index, selected, profile_ctx, targets, flags)

        if not soft_keys:
            logger.warning("第 %d 天七项全硬无解，尝试降级结构项", day_index)

    hint = ("这是求解超时，稍后重试即可" if "求解超时" in last_error
            else "请放宽偏好或忌口后再试")
    raise PlannerError(
        f"第 {day_index} 天排不出七项都达标的餐食（{last_error}）。"
        f"当前忌口 {restrictions or '无'} 下需同时满足热量 "
        f"{targets.get('calorie_min')}~{targets.get('calorie_max')} kcal、"
        f"蛋白 {targets.get('protein_min_g')}~{targets.get('protein_max_g')} g 等区间；"
        f"{hint}。"
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
    targets = plan_targets.build_targets(profile_ctx)
    track = meta if meta is not None else {}
    plan_days: list[PlanDay] = []
    for day_index in range(1, days + 1):
        plan_days.append(build_day(profile_ctx, day_index, days, plan_days, note, track))

    summary = (
        f"约束求解器已按每日 {targets['calorie_min']}~{targets['calorie_max']} kcal 生成 {days} 天方案"
        f"（蛋白 {targets['protein_min_g']}~{targets['protein_max_g']} g、脂肪 "
        f"{targets['fat_min_g']}~{targets['fat_max_g']} g、碳水 "
        f"{targets['carb_min_g']}~{targets['carb_max_g']} g、纤维 ≥{targets['fiber_min_g']} g；"
        f"饮水 {targets['water_min_ml']} ml，睡眠 {targets['sleep_min_h']:g} 小时，"
        f"运动消耗不低于 {targets['exercise_burn_min']} kcal）"
    )

    if track.get("variety_relaxed"):
        summary += "；为避免忌口组合无解，已放宽跨天不重复要求"
    if track.get("soft"):
        summary += f"；结构项降级：{'、'.join(track['soft'])}（菜库数据不完整，已在每日详情标注）"

    intent_summary = str(profile_ctx.get("intent_summary") or "").strip()
    if intent_summary:
        summary += f"；已结合对话：{intent_summary}"

    return PlanResult(
        summary=summary,
        daily_target_calories=target,
        days=plan_days,
        targets=targets,
    )
