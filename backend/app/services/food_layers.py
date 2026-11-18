"""把食物库映射到《中国居民膳食指南(2022)》平衡膳食宝塔的五层

为什么要有这一层：
求解器现在只有热量和七项营养的数值约束，不知道「这一餐该由什么构成」，
于是用大碗米饭 + 大块肉去凑热量——蔬菜只剩西兰花、奶和豆一口没有。
宝塔分层是让「膳食均衡」变成可计算约束的前提。

分层依据（不靠关键词瞎猜）：
1. TFDA 的 category_l2 是官方食物分类（穀物類 / 蔬菜類 / 魚貝類 …），直接映射；
2. 菜（dish）没有官方分类时，用配料表 ingredients 里主料的分类继承；
3. 仍然拿不到的，才用名称规则兜底，并标记 confidence 供人工复核。

生熟口径：
指南的克重是「可食部生重」，食物库多数是熟重。这里不拍脑袋定系数，
按热量守恒反推——同一食材生熟之间只有水分增减，营养总量不变：
    生重 = 熟重 × (熟品 kcal/100g ÷ 生品 kcal/100g)
例如米饭 400 g（116 kcal/100g）= 大米 134 g（346 kcal/100g）。
找不到对应生条目时，退回该层的经验系数，并在结果里标注 used_fallback。
"""
from __future__ import annotations

import logging
import re
from typing import Any

from app.services.llm import foods_data

logger = logging.getLogger("food_layers")

# ---- 宝塔层 ----
GRAIN = "grain"      # 谷薯类（含全谷杂豆、薯类）
VEG = "veg"          # 蔬菜类（含菌藻）
FRUIT = "fruit"      # 水果类
MEAT = "meat"        # 畜禽肉
FISH = "fish"        # 水产
EGG = "egg"          # 蛋类
DAIRY = "dairy"      # 奶及奶制品
BEAN = "bean"        # 大豆及坚果
OIL = "oil"          # 烹调油
SUGAR = "sugar"      # 添加糖
CONDIMENT = "condiment"   # 调味料（不计入配额）
DRINK = "drink"      # 饮料（不计入配额）
OTHER = "other"      # 未分类

# 虚拟层：畜禽 + 水产 + 蛋 合起来算「动物性食物」。
# 膳食指南给的是「畜禽 40–75 + 水产 40–75 + 蛋 40–50」，但没人按三份都吃满来排菜，
# 实际执行口径是动物性食物总量 120–200 g/天，鱼和肉可以互相轮换。
ANIMAL = "animal"

LAYER_NAMES = {
    GRAIN: "谷薯类", VEG: "蔬菜类", FRUIT: "水果类", MEAT: "畜禽肉",
    FISH: "水产", EGG: "蛋类", DAIRY: "奶及奶制品", BEAN: "大豆坚果",
    OIL: "烹调油", SUGAR: "添加糖", CONDIMENT: "调味料", DRINK: "饮料",
    ANIMAL: "动物性食物", OTHER: "未分类",
}

# 动物性食物的组成层。蛋单独卡（指南「每天一个鸡蛋」），不并进来——
# 并进来会同时受「蛋 34–72」和「动物性食物 110–235」两道上限，把肉的配额挤掉，
# 结果就是蛋白顶不到下限、脂肪先超。
ANIMAL_LAYERS = (MEAT, FISH)

# TFDA 官方分类 → 宝塔层
LAYER_OF_L2: dict[str, str] = {
    "穀物類": GRAIN,
    "澱粉類": GRAIN,
    "蔬菜類": VEG,
    "菇類": VEG,
    "藻類": VEG,
    "水果類": FRUIT,
    "肉類": MEAT,
    "魚貝類": FISH,
    "蛋類": EGG,
    "乳品類": DAIRY,
    "豆類": BEAN,
    "堅果及種子類": BEAN,
    "油脂類": OIL,
    "糖類": SUGAR,
    "調味料及香辛料類": CONDIMENT,
    "飲料類": DRINK,
}

# 名称规则兜底：顺序敏感，先匹配到的先用
NAME_RULES: list[tuple[str, tuple[str, ...]]] = [
    (DAIRY, ("牛奶", "酸奶", "奶酪", "芝士", "乳酪", "奶昔", "炼乳", "淡奶油", "乳")),
    (EGG, ("鸡蛋", "鸭蛋", "鹅蛋", "鹌鹑蛋", "皮蛋", "咸蛋", "蛋清", "蛋黄")),
    (CONDIMENT, ("盐", "酱油", "生抽", "老抽", "醋", "料酒", "豆瓣酱", "甜面酱", "蚝油",
                 "味精", "鸡精", "八角", "花椒", "桂皮", "香叶", "孜然", "胡椒", "辣椒粉",
                 "淀粉", "生粉", "酵母", "小苏打", "酱")),
    (DRINK, ("可乐", "雪碧", "汽水", "果汁", "咖啡", "奶茶", "茶饮", "矿泉水", "苏打水",
             "啤酒", "白酒", "红酒", "黄酒", "运动饮料", "功能饮料")),
    # 主食先判：复合菜里「面 / 饭 / 粉」通常是主导层（牛肉面归谷薯，不是畜禽）
    (GRAIN, ("米饭", "糙米", "大米", "糯米", "粳米", "籼米", "黑米", "紫米", "米线",
             "河粉", "肠粉", "粉丝", "粥", "面", "粉", "馒头", "包子", "饺子", "馄饨",
             "饼", "面包", "吐司", "燕麦", "麦片", "玉米", "小米", "高粱", "荞麦",
             "藜麦", "薏米", "年糕", "汤圆", "粽", "土豆", "马铃薯", "红薯", "紫薯",
             "山药", "芋", "莲藕", "藕", "糕")),
    (FRUIT, ("苹果", "香蕉", "橙", "橘", "柑", "柚", "梨", "桃", "李", "杏", "葡萄",
             "提子", "莓", "樱桃", "西瓜", "哈密瓜", "甜瓜", "芒果", "菠萝", "凤梨",
             "猕猴桃", "火龙果", "石榴", "柿子", "枣", "荔枝", "龙眼", "枇杷", "柠檬",
             "牛油果", "椰子", "无花果", "百香果", "山竹", "榴莲", "杨梅", "桑葚")),
    (FISH, ("鱼", "虾", "蟹", "贝", "蛤", "蚝", "牡蛎", "鱿", "章鱼", "海参", "鲍",
            "海带", "紫菜", "裙带", "鳝", "鳗", "三文", "金枪", "鳕", "带子", "扇贝")),
    (BEAN, ("豆腐", "豆干", "豆皮", "腐竹", "豆浆", "豆芽", "毛豆", "黄豆", "黑豆",
            "绿豆", "红豆", "鹰嘴豆", "纳豆", "素鸡", "花生", "核桃", "杏仁", "腰果",
            "芝麻", "瓜子", "开心果", "榛子", "巴旦木", "松子")),
    (MEAT, ("猪肉", "猪排", "猪蹄", "猪肝", "猪肚", "猪血", "猪皮", "猪骨",
            "牛肉", "牛排", "牛腩", "牛柳", "牛腱", "肥牛", "牛杂", "牛骨",
            "羊肉", "羊排", "羊腿", "羊蝎",
            "鸡肉", "鸡腿", "鸡翅", "鸡胸", "鸡爪", "鸡胗", "鸭肉", "鸭腿", "鹅肉",
            "肉", "排骨", "里脊", "五花", "培根", "火腿", "香肠", "腊",
            "酱肘", "肘子", "叉烧", "烧肉", "卤味", "肉松")),
    (VEG, ("菜", "蔬", "瓜", "茄", "椒", "萝卜", "葱", "蒜", "姜", "笋", "菇", "菌",
           "耳", "苔", "苗", "芽", "芹", "菠菜", "油菜", "白菜", "甘蓝", "芥", "茼蒿",
           "韭", "豆角", "四季豆", "豌豆", "秋葵", "苦瓜", "冬瓜", "南瓜", "黄瓜",
           "丝瓜", "西葫芦", "番茄", "西红柿", "洋葱", "西兰花", "花椰菜", "芦笋",
           "莴笋", "生菜", "紫甘蓝", "芹菜")),
    (OIL, ("油", "黄油", "奶油", "猪油", "橄榄油", "花生油", "调和油")),
    (SUGAR, ("白糖", "砂糖", "红糖", "冰糖", "蜂蜜", "糖浆", "代糖")),
]

# 名字里带这些词但**不代表**该层：「鱼香肉丝」没有鱼、「虎皮青椒」没有虎
NAME_TRAP_WORDS = ("鱼香", "虎皮", "蚂蚁上树", "狮子头", "夫妻肺片")


# 生熟折算的兜底系数（只在找不到对应生/熟条目时用）
RAW_FALLBACK_FACTOR = {GRAIN: 2.5, MEAT: 1.25, FISH: 1.25}

# 不需要生熟折算的层：
#   蔬菜水果菇藻 —— 烹调前后可食部重量基本 1:1，硬折反而会把「泡发木耳」算成十几倍；
#   蛋、奶 —— 直接按可食部重量计。
NO_RAW_CONVERSION = {VEG, FRUIT, EGG, DAIRY}

# 豆制品 → 大豆当量系数（《中国居民膳食指南》食物交换份口径：
# 豆腐 100 g ≈ 大豆 20 g，豆干/干丝含水少、当量更高）
SOY_EQUIVALENT: dict[str, float] = {
    "豆浆": 0.05, "豆腐": 0.20, "嫩豆腐": 0.18, "内酯豆腐": 0.15,
    "豆干": 0.45, "干丝": 0.45, "百叶": 0.45, "千张": 0.45, "豆腐皮": 0.40,
    "腐竹": 0.50, "素鸡": 0.35, "纳豆": 0.50, "豆腐脑": 0.10,
}

# 复合菜主料层占比
MAIN_INGREDIENT_SHARE = 0.7

_COOKED_SUFFIX = re.compile(
    r"[（(](熟|生|水煮|蒸|烤|煎|炸|凉拌|焯|炒|燉|炖|滷|卤|薄鹽|薄盐|去皮|切片|切块)[）)]"
)
_COOK_PREFIX = re.compile(r"^(蒸|煮|烤|煎|炸|凉拌|清蒸|白灼|水煮|红烧|清炒|蒜蓉|蚝油)")

# 成品 → 生原料：「米饭」在库里找不到同名的生条目，但它的原料是大米。
# 有了这一层对应，生重折算才能走热量守恒，而不是退回一个拍出来的系数。
RAW_EQUIVALENT: dict[str, str] = {
    "米饭": "大米", "白米饭": "大米", "糙米饭": "大米", "杂粮饭": "大米",
    "馒头": "面粉", "花卷": "面粉", "面条": "面粉", "清汤面条": "面粉",
    "牛肉拉面": "面粉", "面包": "面粉", "全麦面包": "面粉", "吐司": "面粉",
    "米粉": "大米", "米线": "大米", "河粉": "大米", "肠粉": "大米",
    "白粥": "大米", "小米粥": "小米",
}

# 名字 → 生原料 的关键词兜底（比 RAW_EQUIVALENT 粗，但覆盖面大）
RAW_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("饭", "大米"), ("粥", "大米"), ("米粉", "大米"), ("米线", "大米"), ("河粉", "大米"),
    ("面粉", "面粉"), ("面", "面粉"), ("馒头", "面粉"), ("饼", "面粉"),
    ("包", "面粉"), ("饺", "面粉"), ("馄饨", "面粉"), ("吐司", "面粉"),
)


def canonical_name(name: str) -> str:
    """去掉烹调形态，归一到同一个食材名：「土豆(熟)」「蒸土豆」「土豆」→ 土豆"""
    base = _COOKED_SUFFIX.sub("", str(name or "")).strip()
    stripped = _COOK_PREFIX.sub("", base).strip()
    return stripped or base


class FoodLayerIndex:
    """食物 → 宝塔层 的查询索引（进程内缓存，食物库不变就不重建）"""

    def __init__(self, foods: list[dict[str, Any]] | None = None):
        self.foods = foods if foods is not None else foods_data.load_foods()
        self.by_name = {f["name"]: f for f in self.foods}
        self.by_canonical: dict[str, dict] = {}
        for food in self.foods:
            key = canonical_name(food["name"])
            # 生条目优先入索引：拿它做分类来源比熟条目更准
            current = self.by_canonical.get(key)
            if current is None or (current.get("state") == "cooked"
                                   and food.get("state") != "cooked"):
                self.by_canonical[key] = food
        self._cache: dict[str, tuple[str, str]] = {}

    # ---- 单条判定 ----
    def layer_of(self, food: dict[str, Any]) -> tuple[str, str]:
        """返回 (层, 置信度)。置信度：official / inherited / ingredient / name / none"""
        name = str(food.get("name") or "")
        cached = self._cache.get(name)
        if cached:
            return cached

        layer = LAYER_OF_L2.get(str(food.get("category_l2") or ""))
        if layer:
            result = (layer, "official")
            self._cache[name] = result
            return result

        # 从同名生条目的官方分类继承
        twin = self.by_canonical.get(canonical_name(name))
        if twin is not None and twin is not food:
            layer = LAYER_OF_L2.get(str(twin.get("category_l2") or ""))
            if layer:
                result = (layer, "inherited")
                self._cache[name] = result
                return result

        # 菜：用主料（配料表第一个能找到分类的）继承
        for ingredient in food.get("ingredients") or []:
            ref = self.by_name.get(ingredient) or self.by_canonical.get(canonical_name(ingredient))
            if ref is None:
                continue
            layer = LAYER_OF_L2.get(str(ref.get("category_l2") or "")) or self._name_rule(ingredient)
            if layer != OTHER:
                result = (layer, "ingredient")
                self._cache[name] = result
                return result

        layer = self._name_rule(name)
        result = (layer, "name" if layer != OTHER else "none")
        self._cache[name] = result
        return result

    @staticmethod
    def _name_rule(text: str) -> str:
        cleaned = str(text or "")
        for trap in NAME_TRAP_WORDS:
            cleaned = cleaned.replace(trap, "")
        for layer, keywords in NAME_RULES:
            if any(keyword in cleaned for keyword in keywords):
                return layer
        return OTHER

    # ---- 生重折算 ----
    def to_pagoda_weight(self, name: str, cooked_g: float) -> tuple[float, str]:
        """把一道菜的克重换算成能直接和宝塔区间比较的口径。

        - 蔬菜/水果/蛋/奶：直接用可食部重量（烹调前后基本 1:1）
        - 谷物、畜禽、水产：熟重按热量守恒折回生重（米饭 400 g ≈ 大米 134 g）
        - 豆制品：按大豆当量折算（豆腐 100 g ≈ 大豆 20 g）
        """
        food = self.by_name.get(name)
        if food is None:
            return cooked_g, "unknown"
        layer, _ = self.layer_of(food)

        if layer in NO_RAW_CONVERSION:
            return cooked_g, "as_is"

        if layer == BEAN:
            for keyword, ratio in SOY_EQUIVALENT.items():
                if keyword in name:
                    return cooked_g * ratio, "soy_equivalent"
            return cooked_g, "as_is"

        if food.get("state") != "cooked":
            return cooked_g, "raw"

        twin = self.by_canonical.get(canonical_name(name))
        if twin is None or twin is food:
            # 成品（米饭/馒头/粥）在库里没有同名生条目，用它的原料来折算
            raw_name = self._raw_equivalent(name)
            if raw_name:
                twin = self.by_name.get(raw_name)
        cooked_kcal = _kcal(food)
        if twin is not None and twin is not food:
            raw_kcal = _kcal(twin)
            if cooked_kcal and raw_kcal and twin.get("state") != "cooked":
                return cooked_g * cooked_kcal / raw_kcal, "calorie_conserved"

        factor = RAW_FALLBACK_FACTOR.get(layer, 1.0)
        return cooked_g / factor if factor else cooked_g, "fallback"

    def to_raw_weight(self, name: str, cooked_g: float) -> tuple[float, str]:
        """兼容旧调用：只做生熟折算，不碰豆制品当量"""
        return self.to_pagoda_weight(name, cooked_g)

    @staticmethod
    def _raw_equivalent(name: str) -> str | None:
        if name in RAW_EQUIVALENT:
            return RAW_EQUIVALENT[name]
        for keyword, raw in RAW_KEYWORDS:
            if keyword in name:
                return raw
        return None


def _kcal(food: dict[str, Any]) -> float:
    try:
        return float((food.get("per_100g") or {}).get("calories") or 0)
    except (TypeError, ValueError):
        return 0.0


_INDEX: FoodLayerIndex | None = None


# 《中国居民膳食指南(2022)》平衡膳食宝塔，按 1800 kcal 能量档取推荐量（可食部生重，克）。
# 宝塔本身分 1600/1800/2000/2200/2400 五档，用固定区间套所有热量会出现
# 「各层都取下限、加起来仍超热量上限」的死结，所以按能量档取点值再给容差。
PAGODA_POINT: dict[str, float] = {
    GRAIN: 275,   # 谷类 225 + 薯类 50
    VEG: 400,
    FRUIT: 200,
    ANIMAL: 145,  # 畜禽 50 + 水产 50 + 蛋 45 合计的动物性食物
    EGG: 45,      # 指南明确「每天一个鸡蛋」，不并入轮换
    DAIRY: 300,
    BEAN: 25,     # 大豆 15 + 坚果 10
    OIL: 25,
}
PAGODA_MIN_RATIO = 0.75   # 低于推荐量 75% 算没吃到
PAGODA_MAX_RATIO = 1.6    # 高于 160% 算过量（防「1400 g 西兰花」这种凑数解）
SALT_MAX_G = 5.0
# 全日烹调用油（指南 25–30 g）。食物库的菜不含烹饪用油，这一项是「每天固定量」，
# 不靠选菜满足，所以统计时直接按 25 g 计入。
COOKING_OIL_G = 25.0

# 每天吃到的食物种类（不含油盐调料）——指南要求平均每天 12 种以上、每周 25 种以上
SPECIES_PER_DAY_MIN = 12

# 复合菜整盘重量里算给主料层的比例；单一食材的菜按 100% 计
DISH_MAIN_SHARE = 0.7


def pagoda_range(calorie_target: float | None = None) -> dict[str, tuple[float, float]]:
    """按热量档给出各层的可接受区间。

    1800 kcal 是基准档；能量越高，各层按比例上浮（指南 2000/2200/2400 档的走向一致），
    但不按线性放大——奶和蛋是「每天固定量」，不随热量变。
    """
    scale = 1.0
    if calorie_target:
        scale = min(1.25, max(0.85, float(calorie_target) / 1800.0))
    out: dict[str, tuple[float, float]] = {}
    for layer, point in PAGODA_POINT.items():
        fixed = layer in (DAIRY, EGG)      # 每天固定量的层不随热量缩放
        value = point if fixed else point * scale
        out[layer] = (round(value * PAGODA_MIN_RATIO, 1),
                      round(value * PAGODA_MAX_RATIO, 1))
    return out


def pagoda_status(layer: str, grams: float, calorie_target: float | None = None) -> tuple[str, float, float]:
    low, high = pagoda_range(calorie_target)[layer]
    status = "low" if grams < low else "high" if grams > high else "ok"
    return status, low, high


def main_layer_share(food: dict[str, Any]) -> float:
    """这道菜有多少分量算在它的主料层上。

    只有「跨了两个及以上配额层」的菜才需要分摊（青椒肉丝 = 蔬菜 + 畜禽）；
    凉拌木耳这种主料和配菜都落在蔬菜层的，整盘都算蔬菜。
    """
    if food.get("food_type") in ("ingredient", "single_food"):
        return 1.0
    ingredients = food.get("ingredients") or []
    if len(ingredients) <= 1:
        return 1.0

    idx = index()
    layers: set[str] = set()
    for name in ingredients:
        ref = idx.by_name.get(name) or idx.by_canonical.get(canonical_name(name))
        if ref is None:
            continue
        layer = idx.layer_of(ref)[0]
        if layer in PAGODA_POINT or layer in ANIMAL_LAYERS:
            layers.add(ANIMAL if layer in ANIMAL_LAYERS else layer)
    return DISH_MAIN_SHARE if len(layers) >= 2 else 1.0




def day_profile(meals: list[dict], targets: dict | None = None) -> dict:
    """把一天的餐食换算成「宝塔五层各吃了多少生重」+ 食物种类数。

    每层都是可食部生重，口径和 PAGODA 一致，可以直接比。
    """
    idx = index()
    raw: dict[str, float] = {layer: 0.0 for layer in PAGODA_POINT}
    raw[ANIMAL] = 0.0
    raw[OIL] = COOKING_OIL_G      # 全日烹调用油按固定量计
    species: set[str] = set()
    total_kcal = 0.0
    for meal in meals or []:
        for item in meal.get("items") or []:
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            grams = float(item.get("portion_g") or 0)
            food = idx.by_name.get(name)
            layer = idx.layer_of(food)[0] if food else OTHER
            if food:
                total_kcal += _kcal(food) * grams / 100.0
            if layer in ANIMAL_LAYERS or layer in PAGODA_POINT:
                share = main_layer_share(food) if food else DISH_MAIN_SHARE
                grams_raw = idx.to_pagoda_weight(name, grams)[0] * share
                if layer in ANIMAL_LAYERS:
                    raw[ANIMAL] += grams_raw
                if layer != OIL:      # 烹调油按固定量计，不累加菜品里的油
                    raw[layer] = raw.get(layer, 0.0) + grams_raw
                species.add(canonical_name(name))

    scale = _calorie_hint(meals)
    ranges = pagoda_range(scale)
    layers = {}
    for layer, (low, high) in ranges.items():
        value = round(raw.get(layer, 0.0), 1)
        layers[layer] = {
            "name": LAYER_NAMES[layer],
            "raw_g": value,
            "min": low, "max": high,
            "status": ("low" if value < low else "high" if value > high else "ok"),
        }
    # 单层明细仍要给出：畜禽/水产/蛋分开看才知道是不是只有猪肉没有鱼
    for layer in ANIMAL_LAYERS:
        layers[layer] = {
            "name": LAYER_NAMES[layer],
            "raw_g": round(raw.get(layer, 0.0), 1),
            "min": None, "max": None, "status": "detail",
        }

    return {
        "layers": layers,
        "species": len(species),
        "species_ok": len(species) >= SPECIES_PER_DAY_MIN,
        "calories": round(total_kcal, 0) if total_kcal else None,
    }


def _calorie_hint(meals: list[dict]) -> float | None:
    """从餐食本身估一个热量档（含烹调油），用于选宝塔能量档。

    求解器的 target 是含 25 g 烹调油的口径，这里也必须含——否则同一份餐食
    在求解侧判达标、体检侧判超标（水果 300 g 在两个口径下一个是 ok 一个是 high）。
    """
    total = 0.0
    idx = index()
    for meal in meals or []:
        for item in meal.get("items") or []:
            food = idx.by_name.get(str(item.get("name") or "").strip())
            if food:
                total += _kcal(food) * float(item.get("portion_g") or 0) / 100.0
    return (total + COOKING_OIL_G * 9.0) or None


def index() -> FoodLayerIndex:
    global _INDEX
    if _INDEX is None:
        _INDEX = FoodLayerIndex()
    return _INDEX


def layer_of(food: dict[str, Any]) -> str:
    return index().layer_of(food)[0]


def to_raw_weight(name: str, cooked_g: float) -> float:
    return index().to_raw_weight(name, cooked_g)[0]


def coverage_report() -> dict:
    """分层覆盖率：还有多少条没归到层，列出来给人复核"""
    idx = index()
    stats: dict[str, int] = {}
    confidence: dict[str, int] = {}
    unclassified: list[str] = []
    for food in idx.foods:
        layer, conf = idx.layer_of(food)
        stats[layer] = stats.get(layer, 0) + 1
        confidence[conf] = confidence.get(conf, 0) + 1
        if layer == OTHER:
            unclassified.append(food["name"])
    return {
        "total": len(idx.foods),
        "by_layer": {LAYER_NAMES.get(k, k): v for k, v in
                     sorted(stats.items(), key=lambda kv: -kv[1])},
        "by_confidence": confidence,
        "unclassified": unclassified,
    }
