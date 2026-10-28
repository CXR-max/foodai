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
}


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
    }
