"""运动热量消耗计算（个性化版）

第一层　基础消耗：MET × 体重(kg) × 时长(分钟) ÷ 60
        MET 不再是单一值，每种运动分"轻松/适中/吃力"三档，
        取值参考 Compendium of Physical Activities 常用近似值。

第二层　个体化系数（全部来自健康档案，系数都限制在合理区间，保证可解释）：
        - 性别：同强度下女性净消耗约低 5%（体成分/血红蛋白差异）→ ×0.95
        - 年龄：以 30 岁为基准，每大 10 岁同强度消耗约低 2% → 系数钳制 0.90~1.05
        - 体成分：肌肉是主要耗能组织，用"去脂体重占比"修正；
          未填体脂率时按 Deurenberg 公式由 BMI/年龄/性别估算，档案不全则 ×1.0
        - 体能水平：动作经济性，越熟练越省力 → 久坐 1.05 / 轻度 1.02 /
          中度 1.00 / 高度 0.97 / 运动员 0.94

输出不是单点而是区间（±10%），并附完整明细供前端展示"这个数怎么来的"。
"""
INTENSITY_LABELS = {"light": "轻松", "moderate": "适中", "intense": "吃力"}
INTENSITY_ORDER = ("light", "moderate", "intense")

# 每种运动的 MET 三档：(轻松, 适中, 吃力)
MET_LEVELS: dict[str, tuple[float, float, float]] = {
    "散步": (2.6, 3.0, 3.5),
    "瑜伽": (2.5, 3.0, 4.0),
    "快走": (3.5, 4.3, 5.5),
    "跳舞": (4.0, 5.0, 6.5),
    "力量训练": (4.5, 6.0, 7.5),
    "球类": (5.5, 7.0, 8.5),
    "骑行": (5.5, 7.5, 10.0),
    "游泳": (6.0, 8.0, 10.5),
    "跑步": (8.0, 10.0, 12.0),
    "跳绳": (8.0, 11.0, 13.5),
}
DEFAULT_LEVELS = (3.5, 5.0, 7.0)

# 体能水平 → 动作经济性系数（越熟练越省力）
FITNESS_FACTORS = {
    "sedentary": 1.05, "light": 1.02, "moderate": 1.00,
    "active": 0.97, "athlete": 0.94,
}

# 性别 → 同强度净消耗系数
GENDER_FACTORS = {"male": 1.00, "female": 0.95}

# Deurenberg 体脂率估算：bf% = 1.20×BMI + 0.23×年龄 − 10.8×性别(男1女0) − 5.4
# 去脂占比的参考基准：男 78%（体脂 22%）/ 女 72%（体脂 28%）
LEAN_REF = {"male": 0.78, "female": 0.72}


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def get_met(exercise_type: str | None, intensity: str = "moderate") -> float:
    """取某运动某档强度的 MET；未收录类型按同档默认值兜底"""
    idx = INTENSITY_ORDER.index(intensity) if intensity in INTENSITY_ORDER else 1
    levels = MET_LEVELS.get((exercise_type or "").strip(), DEFAULT_LEVELS)
    return levels[idx]


def met_known(exercise_type: str | None) -> bool:
    return (exercise_type or "").strip() in MET_LEVELS


def estimate_body_fat_pct(bmi: float | None, age: int | None, gender: str | None) -> float | None:
    """Deurenberg 公式估算体脂率（档案没填体脂率时的降级方案）"""
    if not bmi or not age:
        return None
    sex = 1.0 if (gender or "male") == "male" else 0.0
    return round(clamp(1.20 * bmi + 0.23 * age - 10.8 * sex - 5.4, 4.0, 60.0), 1)


def calc_factors(profile: dict | None) -> list[dict]:
    """根据档案算四个个体化系数，返回明细列表（前端直接展示）

    profile 需要的字段：gender / age / weight_kg / height_cm / body_fat_pct / activity_level
    缺哪个就按中性值处理（×1.0），绝不因为档案不全而报错。
    """
    p = profile or {}
    gender = p.get("gender") or "male"
    age = p.get("age")
    factors: list[dict] = []

    # 1) 性别
    g = GENDER_FACTORS.get(gender, 1.00)
    factors.append({"key": "gender", "label": "性别", "value": round(g, 3),
                    "note": "男" if gender == "male" else "女"})

    # 2) 年龄：以 30 岁为基准，每大 10 岁约 -2%
    if age:
        a = clamp(1.0 - (age - 30) * 0.002, 0.90, 1.05)
        factors.append({"key": "age", "label": "年龄", "value": round(a, 3), "note": f"{age} 岁"})
    else:
        factors.append({"key": "age", "label": "年龄", "value": 1.0, "note": "未填写"})

    # 3) 体成分：去脂占比 vs 同性别基准；没填体脂率就按 BMI 估算
    bf = p.get("body_fat_pct")
    bf_source = "档案填写"
    if not bf:
        bmi = None
        if p.get("weight_kg") and p.get("height_cm"):
            bmi = p["weight_kg"] / (p["height_cm"] / 100) ** 2
        bf = estimate_body_fat_pct(bmi, age, gender)
        bf_source = "按 BMI 估算"
    if bf:
        lean_ratio = 1 - bf / 100
        ref = LEAN_REF.get(gender, 0.75)
        b = clamp(lean_ratio / ref, 0.90, 1.12)
        factors.append({"key": "body_fat", "label": "体成分", "value": round(b, 3),
                        "note": f"体脂 {round(bf, 1)}% · {bf_source}"})
    else:
        factors.append({"key": "body_fat", "label": "体成分", "value": 1.0, "note": "身高体重不全，未修正"})

    # 4) 体能水平（动作经济性）
    al = p.get("activity_level") or "moderate"
    f = FITNESS_FACTORS.get(al, 1.00)
    al_labels = {"sedentary": "久坐少动", "light": "轻度活动", "moderate": "中度活动",
                 "active": "高度活动", "athlete": "运动员级"}
    factors.append({"key": "fitness", "label": "体能水平", "value": round(f, 3),
                    "note": al_labels.get(al, al)})

    return factors


def personalized_burn(exercise_type: str | None, duration_min: int,
                      profile: dict | None = None, intensity: str = "moderate") -> dict:
    """个性化运动消耗：基础 MET 值 × 体重 × 时长，再逐层乘个体系数

    返回：基础值、逐层系数明细、最终消耗、±10% 区间、人类可读公式。
    """
    if not duration_min or duration_min <= 0:
        return {"calories_burned": 0.0, "low": 0.0, "high": 0.0, "factors": [],
                "met": 0, "base": 0.0, "formula": ""}

    weight = (profile or {}).get("weight_kg") or 60.0
    met = get_met(exercise_type, intensity)
    base = round(met * weight * duration_min / 60.0, 1)

    factors = calc_factors(profile)
    total = base
    for f in factors:
        total *= f["value"]
    total = round(total, 1)

    # 明细行：基础值 + 每层系数的连乘过程
    steps = [f"基础 {met} MET × {round(weight, 1)}kg × {duration_min}min ÷ 60 = {base} kcal"]
    running = base
    for f in factors:
        running = round(running * f["value"], 1)
        steps.append(f"{f['label']}（{f['note']}）×{f['value']} → {running} kcal")

    return {
        "exercise_type": exercise_type,
        "duration_min": duration_min,
        "intensity": intensity,
        "intensity_label": INTENSITY_LABELS.get(intensity, "适中"),
        "met": met,
        "met_known": met_known(exercise_type),
        "weight_kg": round(weight, 1),
        "base": base,
        "factors": factors,
        "steps": steps,
        "calories_burned": total,
        "low": round(total * 0.9, 1),
        "high": round(total * 1.1, 1),
        "formula": f"{met} MET × {round(weight, 1)}kg × {duration_min}min ÷ 60"
                   + "".join(f" ×{f['value']}" for f in factors),
    }


def calc_burn(exercise_type: str | None, duration_min: int, weight_kg: float = 60.0) -> float:
    """兼容旧调用：不带档案的简化版（适中强度、无个体系数），保留 1 位小数"""
    if not duration_min or duration_min <= 0:
        return 0.0
    return round(get_met(exercise_type, "moderate") * (weight_kg or 60.0) * duration_min / 60.0, 1)
