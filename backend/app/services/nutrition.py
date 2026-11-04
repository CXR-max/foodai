"""
营养与身体指标计算服务（"视觉→营养"管线的枢纽）
- 身体指标：BMI / BMR(Mifflin-St Jeor) / 每日热量目标
- 识别→营养：菜名匹配营养库 → 按克重换算营养值（mock 与真模型共用同一管线）
"""
from app.services.llm import foods_data

# 活动系数（BMR → 每日总消耗 TDEE）
ACTIVITY_FACTORS = {
    "sedentary": 1.2,    # 久坐少动
    "light": 1.375,      # 轻度活动（每周运动1-3次）
    "moderate": 1.55,    # 中度活动（每周3-5次）
    "active": 1.725,     # 高度活动（每周6-7次）
    "athlete": 1.9,      # 运动员级/体力劳动
}


# ================= 身体指标 =================

def calc_bmi(weight_kg: float | None, height_cm: float | None) -> float | None:
    """BMI = 体重kg / 身高m²"""
    if not weight_kg or not height_cm or height_cm <= 0:
        return None
    return round(weight_kg / (height_cm / 100) ** 2, 1)


def bmi_level(bmi: float | None) -> str:
    """BMI 中国标准分级"""
    if bmi is None:
        return "未知"
    if bmi < 18.5:
        return "偏瘦"
    if bmi < 24:
        return "正常"
    if bmi < 28:
        return "超重"
    return "肥胖"


def calc_bmr(weight_kg, height_cm, age, gender) -> float | None:
    """基础代谢率（Mifflin-St Jeor 公式）：男 10W+6.25H-5A+5 / 女 -161"""
    if not (weight_kg and height_cm and age):
        return None
    bmr = 10 * weight_kg + 6.25 * height_cm - 5 * age
    bmr += 5 if gender == "male" else -161
    return round(bmr)


def calc_daily_target(
    weight_kg, height_cm, age, gender, activity_level: str = "moderate", goal: str = "maintain"
) -> int | None:
    """每日热量目标 = BMR × 活动系数 ± 目标调整（减脂-400 / 增肌+300），下限 1200"""
    bmr = calc_bmr(weight_kg, height_cm, age, gender)
    if bmr is None:
        return None
    tdee = bmr * ACTIVITY_FACTORS.get(activity_level, 1.55)
    if goal == "lose":
        tdee -= 400
    elif goal == "gain":
        tdee += 300
    return max(int(tdee), 1200)


# ================= 识别 → 营养换算 =================

# 份量档位系数：模型只判"相对常见一份的大小"，绝对克重用本地标准份量校准（误差可控）
SIZE_FACTORS = {"small": 0.7, "normal": 1.0, "large": 1.3}
# 未收录菜品：接受模型克重，但限制在合理区间（防离谱幻觉）
PORTION_CLAMP = (50, 1500)


def resolve_portion(dish_name: str, model_portion_g: float, size_class: str = "normal") -> float:
    """档位制克数校准：命中营养库 → 标准份 × 档位系数（限 ±50%）；未命中 → 模型克重 clamp"""
    entry = foods_data.find_food(dish_name)
    if entry and entry.get("common_portion_g"):
        base = float(entry["common_portion_g"])
        factor = SIZE_FACTORS.get((size_class or "").strip().lower(), 1.0)
        g = min(max(base * factor, base * 0.5), base * 1.6)
        return round(g)
    lo, hi = PORTION_CLAMP
    return round(min(max(model_portion_g or 0, lo), hi))


def match_and_calc(dish_name: str, portion_g: float, category: str | None = None) -> dict:
    """
    菜名 + 克重 → 匹配营养库 → 营养值
    命中库：营养值精确且全站口径一致；未命中：按类别均值估算（类别不识别则全库均值）并标记
    返回：{calories, protein_g, fat_g, carb_g, matched, matched_name, match_method, match_score,
           nutrition_source, note}
    """
    match = foods_data.match_entry(dish_name)
    if match:
        entry = match["entry"]
        nut = foods_data.calc_nutrition(entry, portion_g)
        return {
            **nut,
            "matched": True,
            "matched_name": entry["name"],
            "match_method": match["method"],
            "match_score": match["score"],
            "nutrition_source": "dish_library",
            "note": foods_data.describe_match(match),
        }
    # 未收录 → 类别均值（category_average 内部对未知类别自动回退全库均值）× 克重
    avg = foods_data.category_average(category or None)
    factor = max(portion_g, 0) / 100.0
    return {
        "calories": round(avg["calories"] * factor, 1),
        "protein_g": round(avg["protein_g"] * factor, 1),
        "fat_g": round(avg["fat_g"] * factor, 1),
        "carb_g": round(avg["carb_g"] * factor, 1),
        "matched": False,
        "matched_name": None,
        "match_method": "none",
        "match_score": 0.0,
        "nutrition_source": "category_average",
        "note": "菜品暂未收录营养库，按通用估算值记录",
    }
