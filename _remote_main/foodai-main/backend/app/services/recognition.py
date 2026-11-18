"""识别结果的匹配判定：匹配方式、候选列表、是否需要用户确认。

规则（与产品约定一致）：
- 置信度 ≥ 0.6 + 精确/同义/别名命中 + 不在易混淆组 + 份量正常 → 直接出结果
- 置信度 ≥ 0.6，但相似匹配 / 易混淆组 / 未收录 → 出结果，同时弹候选让用户确认
- 置信度 < 0.6 → 必须确认（候选 + 克重）
"""
from app.services.llm import foods_data

CONFIDENCE_THRESHOLD = 0.6
# 相似匹配低于这个分数时，即使模型置信度高也要确认
FUZZY_CONFIRM_THRESHOLD = 0.9

REASON_LABELS = {
    "low_confidence": "识别置信度低于 60%",
    "unmatched": "营养库未收录这道菜，热量是估算值",
    "fuzzy_match": "菜名只是相似匹配，可能认错",
    "confusable": "这道菜和同组菜品容易混淆",
    "contains_match": "菜名只是包含关系匹配，可能不是同一道菜",
}

# 档案禁忌 → 关键词。和前端同一套规则，后端算完放进结果，避免两边口径漂移。
MEAT_WORDS = ["肉", "鸡", "鸭", "鱼", "虾", "蟹", "排骨", "火腿", "腊肉", "牛", "羊"]
ALLERGY_RULES = {
    "海鲜过敏": ["虾", "蟹", "鱼", "贝", "蛤", "鱿", "海参"],
    "花生过敏": ["花生"],
    "坚果过敏": ["花生", "核桃", "杏仁", "腰果", "榛子"],
    "乳糖不耐": ["牛奶", "奶油", "黄油", "芝士", "酸奶"],
    "麸质过敏": ["面粉", "面包", "面条", "馒头", "饺子", "面筋"],
    "大豆过敏": ["豆腐", "豆干", "腐竹", "黄豆", "豆芽", "豆浆", "豆瓣酱"],
    "不吃猪肉": ["猪肉", "五花", "排骨", "里脊", "火腿", "腊肉", "叉烧"],
    "不吃牛肉": ["牛肉", "牛腩"],
    "清真": ["猪肉", "五花", "排骨", "火腿", "腊肉", "料酒"],
}


def dietary_warnings(dish_body: dict, restrictions: list[str]) -> list[str]:
    """按档案禁忌检查一道菜，返回人话提示（只提示，不阻断保存）。

    dish_body: {dish_name, ingredients, dietary_tags, taste_tags, portion_g, nutrition}
    """
    if not restrictions:
        return []
    name = str(dish_body.get("dish_name") or "")
    ingredients = [str(i.get("name") or "") for i in (dish_body.get("ingredients") or [])]
    tags = list(dish_body.get("dietary_tags") or []) + list(dish_body.get("taste_tags") or [])
    text = " ".join([name, *ingredients])
    nutrition = dish_body.get("nutrition") or {}
    portion = float(dish_body.get("portion_g") or 0) or 1.0
    fat_per_100 = (float(nutrition.get("fat_g") or 0) * 100) / portion

    hits: list[str] = []
    for rule in restrictions:
        if rule in {"素食", "蛋奶素"}:
            hit = next((w for w in MEAT_WORDS if w in text), None)
            if hit:
                hits.append(f"{rule}：这道菜含「{hit}」")
            continue
        if rule == "忌辣":
            if any("辣" in t for t in tags) or any(w in text for w in ("辣椒", "花椒", "豆瓣酱")):
                hits.append("忌辣：含辣椒/花椒/豆瓣酱")
            continue
        if rule == "少油":
            if fat_per_100 > 15:
                hits.append(f"少油：脂肪约 {round(fat_per_100)}g/100g")
            continue
        if rule == "低糖":
            if any(w in text for w in ("白糖", "冰糖")):
                hits.append("低糖：含糖调味")
            continue
        if rule == "低盐":
            continue
        words = ALLERGY_RULES.get(rule)
        if not words:
            continue
        hit = next((w for w in words if w in text or any(w in t for t in tags)), None)
        if hit:
            hits.append(f"{rule}：含「{hit}」")
    return list(dict.fromkeys(hits))


def _candidate(food: dict, method: str, score: float, relation: dict | None = None) -> dict:
    per100 = food.get("per_100g") or {}
    return {
        "name": food["name"],
        "category": food.get("category"),
        "common_portion_g": food.get("common_portion_g") or 100,
        "calories_per_100g": per100.get("calories"),
        "match_method": method,
        "score": round(float(score), 3),
        "distinguish_tip": (relation or {}).get("distinguish_tip"),
        "relation_type": (relation or {}).get("relation_type"),
        "group_key": (relation or {}).get("group_key"),
    }


def evaluate(vision, nutrition_result: dict | None = None) -> dict:
    """把一个视觉识别结果判成「可直接采信 / 需要确认」"""
    nutrition_result = nutrition_result or {}
    empty = {
        "query": getattr(vision, "dish_name", ""),
        "matched": False,
        "matched_name": None,
        "method": "none",
        "method_label": "未匹配",
        "score": 0.0,
        "needs_confirm": False,
        "reason_codes": [],
        "reasons": [],
        "candidates": [],
    }
    if not getattr(vision, "is_food", False):
        return empty

    match = foods_data.match_entry(vision.dish_name)
    codes: list[str] = []

    if match is None:
        codes.append("unmatched")
    elif match["method"] == "fuzzy" and match["score"] < FUZZY_CONFIRM_THRESHOLD:
        codes.append("fuzzy_match")
    elif match["method"] == "contains":
        # 包含匹配本质是「菜名里出现了某个词」，一律让用户确认，避免静默算错
        codes.append("contains_match")

    if (getattr(vision, "confidence", 0) or 0) < CONFIDENCE_THRESHOLD:
        codes.append("low_confidence")

    confusables = foods_data.confusable_relations(vision.dish_name) if match else []
    if confusables:
        codes.append("confusable")

    candidates: list[dict] = []
    if codes:
        seen: set[str] = set()
        # 已匹配到的菜放第一位：用户可以「就这样保存」
        if match is not None:
            candidates.append(_candidate(match["entry"], match["method"], match["score"]))
            seen.add(match["entry"]["name"])
        for item in foods_data.find_candidates(vision.dish_name, limit=6):
            name = item["entry"]["name"]
            if name in seen:
                continue
            seen.add(name)
            candidates.append(_candidate(item["entry"], item["match_type"], item["score"], item.get("relation")))
        candidates = candidates[:5]

    return {
        "query": vision.dish_name,
        "matched": match is not None,
        "matched_name": match["entry"]["name"] if match else None,
        "method": match["method"] if match else "none",
        "method_label": foods_data.MATCH_METHOD_LABELS.get(match["method"], "未匹配") if match else "未匹配",
        "score": match["score"] if match else 0.0,
        "needs_confirm": bool(codes),
        "reason_codes": codes,
        "reasons": [REASON_LABELS[code] for code in codes],
        "candidates": candidates,
        "nutrition_source": nutrition_result.get("nutrition_source"),
        # 前端做禁忌/过敏提示用：库内标准食材 + 标签（未命中时为空）
        "dietary_tags": list(match["entry"].get("dietary_tags") or []) if match else [],
        "taste_tags": list(match["entry"].get("taste_tags") or []) if match else [],
        "ingredients": list(match["entry"].get("ingredients") or []) if match else [],
    }
