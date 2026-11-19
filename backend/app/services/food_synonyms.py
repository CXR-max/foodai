"""常见食材同义词：用于“番茄/西红柿”这类用户说法与菜名不一致的场景。"""
from __future__ import annotations

FOOD_SYNONYM_GROUPS = (
    ("番茄", "西红柿"),
    ("土豆", "马铃薯"),
    ("花菜", "菜花"),
    ("西兰花", "绿花菜", "青花菜"),
    ("豆角", "四季豆", "刀豆"),
    ("胡萝卜", "红萝卜"),
    ("卷心菜", "包菜", "圆白菜"),
    ("黄瓜", "青瓜"),
    ("香菜", "芫荽"),
    ("红薯", "地瓜"),
)


def expand_food_terms(terms) -> list[str]:
    """把排除/想吃词扩展到同义词；保持原顺序并去重。"""
    result: list[str] = []
    for term in terms or []:
        term = str(term).strip()
        if not term:
            continue
        if term not in result:
            result.append(term)
        for group in FOOD_SYNONYM_GROUPS:
            if any(word == term or word in term for word in group):
                for synonym in group:
                    if synonym not in result:
                        result.append(synonym)
    return result
