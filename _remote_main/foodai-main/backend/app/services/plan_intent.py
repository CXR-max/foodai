"""
方案意图翻译：把「勾选偏好 + 备注 + AI 对话」翻译成求解器能吃的结构化约束。

大模型只做翻译，不生成菜谱；前端勾选是硬约束，对话只能在它之上追加。
失败（模型没起/JSON 不合法/超时）时退回关键词规则，保证方案还能生成。
"""
from __future__ import annotations

import logging
import re

from app.services.llm import foods_data
from app.services.llm.base import LLMProvider, extract_json

logger = logging.getLogger("plan_intent")

TASTE_LABELS = {
    "清淡", "咸鲜", "香辣", "微辣", "麻辣", "酸辣", "偏甜", "偏酸",
    "偏咸", "蒜香", "孜然", "酱香", "烧烤", "鲜香",
}

RESTRICTION_LABELS = {
    "素食", "蛋奶素", "忌辣", "少油", "低糖", "低盐", "海鲜过敏", "花生过敏",
    "坚果过敏", "乳糖不耐", "麸质过敏", "大豆过敏", "不吃猪肉", "清真", "不吃牛肉",
}

# 对话里可能出现的标准禁忌说法 → 前端标签
RESTRICTION_ALIASES = {
    "吃素": "素食",
    "纯素": "素食",
    "不吃辣": "忌辣",
    "不要辣": "忌辣",
    "怕辣": "忌辣",
    "控糖": "低糖",
    "少盐": "低盐",
    "清淡少油": "少油",
    "海鲜过敏": "海鲜过敏",
    "花生过敏": "花生过敏",
    "坚果过敏": "坚果过敏",
    "乳糖不耐": "乳糖不耐",
    "麸质过敏": "麸质过敏",
    "大豆过敏": "大豆过敏",
    "不吃猪肉": "不吃猪肉",
    "不吃猪": "不吃猪肉",
    "不要猪肉": "不吃猪肉",
    "别放猪肉": "不吃猪肉",
    "清真": "清真",
    "不吃牛肉": "不吃牛肉",
    "不吃牛": "不吃牛肉",
    "不要牛肉": "不吃牛肉",
    "别放牛肉": "不吃牛肉",
    "不吃海鲜": "海鲜过敏",
    "不要海鲜": "海鲜过敏",
}

# 常见菜名/食材词，用于把「牛肉炒饭」拆成 牛肉 + 炒饭
COMMON_FOOD_TERMS = (
    "炒饭", "面条", "饺子", "包子", "粥", "汤", "沙拉", "三明治", "汉堡",
    "披萨", "寿司", "米饭", "馒头", "面包", "鸡蛋", "豆腐", "豆浆", "牛奶",
    "牛肉", "猪肉", "鸡肉", "鱼肉", "羊肉", "虾", "鱼", "蟹", "蔬菜", "青菜",
    "水果", "苹果", "香蕉", "橙子", "西瓜", "葡萄", "红薯", "玉米", "燕麦",
)


def _food_terms() -> tuple[str, ...]:
    terms = set(COMMON_FOOD_TERMS)
    for food in foods_data.load_foods():
        terms.add(str(food.get("name") or ""))
        terms.update(str(item) for item in (food.get("ingredients") or []))
    return tuple(sorted((t for t in terms if t), key=len, reverse=True))


FOOD_TERMS = _food_terms()


def _as_list(value) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _unique(values) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


def _expand_food_terms(values: list[str]) -> list[str]:
    """把“牛肉炒饭”拆成求解器可匹配的 牛肉、炒饭；最多 5 个。"""
    result: list[str] = []
    for value in values:
        matches = [term for term in FOOD_TERMS if term in value]
        if matches:
            result.extend(matches)
        else:
            result.append(value)
    return _unique(result)[:5]


SPICY_TASTES = {"香辣", "微辣", "麻辣", "酸辣"}
NEG_SPICY = ("不想吃辣", "不吃辣", "不要辣", "忌辣", "戒辣", "不能吃辣", "怕辣", "少吃辣")
POS_SPICY = ("想吃辣", "吃辣", "重口", "香辣", "麻辣", "微辣", "酸辣", "辣")
CLEAR_TASTE = ("清淡", "不重口", "少辣")


def _user_segments(text: str) -> list[str]:
    """按时间顺序取用户说过的话（排除 AI 建议，避免把 AI 的推荐当成需求）。"""
    segments: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        match = re.search(r"(?:^|\]\s*)用户[:：]\s*(.*)$", stripped)
        if match:
            segments.append(match.group(1).strip())
            continue
        if stripped.startswith(("AI：", "AI:", "【", "[")):
            continue
        segments.append(stripped)
    return segments


def _taste_clauses(text: str) -> list[str]:
    """把“上周想吃辣但这周不想吃辣”拆成短句，确保取到最新分句。"""
    clauses: list[str] = []
    for segment in _user_segments(text):
        clauses.extend(re.split(r"[，,。；;！!？?]|但是|但|不过|现在|这周|最近", segment))
    return [clause.strip() for clause in clauses if clause.strip()]


def _compose_summary(intent: dict) -> str:
    parts: list[str] = []
    if intent.get("taste_preferences"):
        parts.append("口味：" + "/".join(intent["taste_preferences"][:4]))
    if intent.get("dietary_restrictions"):
        parts.append("禁忌：" + "/".join(intent["dietary_restrictions"][:4]))
    if intent.get("include_foods"):
        parts.append("想吃：" + "/".join(intent["include_foods"][:4]))
    if intent.get("exclude_foods"):
        parts.append("不吃：" + "/".join(intent["exclude_foods"][:4]))
    return "；".join(parts)[:40]


def _apply_latest_wins(intent: dict, text: str) -> tuple[dict, bool]:
    """同一维度冲突时，以时间上最新的一句明确表态为准。

    这是模型输出的兜底：7B 有时会把历史里的“想吃辣”和最新的“不想吃辣”都留下。
    """
    segments = _taste_clauses(text)
    latest = None
    for segment in segments:
        if any(phrase in segment for phrase in NEG_SPICY):
            latest = "neg"
        elif any(phrase in segment for phrase in CLEAR_TASTE):
            latest = "clear"
        elif any(phrase in segment for phrase in POS_SPICY):
            latest = "pos"

    if latest is None:
        return intent, False

    tastes = [t for t in (intent.get("taste_preferences") or []) if t in TASTE_LABELS]
    restrictions = list(intent.get("dietary_restrictions") or [])

    if latest == "neg":
        tastes = [t for t in tastes if t not in SPICY_TASTES]
        if "忌辣" not in restrictions:
            restrictions.append("忌辣")
    elif latest == "clear":
        tastes = [t for t in tastes if t not in SPICY_TASTES]
        if "清淡" not in tastes:
            tastes.append("清淡")
    else:  # latest == "pos"
        tastes = [t for t in tastes if t != "清淡"]
        restrictions = [r for r in restrictions if r != "忌辣"]
        if not any(t in SPICY_TASTES for t in tastes):
            tastes.append("香辣")

    intent = dict(intent)
    intent["taste_preferences"] = _unique(tastes)
    intent["dietary_restrictions"] = _unique(restrictions)
    return intent, True


def _normalize(raw: dict, text: str) -> dict:
    raw = raw if isinstance(raw, dict) else {}

    tastes = [
        item for item in _as_list(raw.get("taste_preferences") or raw.get("taste"))
        if item in TASTE_LABELS
    ]
    restrictions = [
        item for item in _as_list(
            raw.get("dietary_restrictions") or raw.get("restrictions")
        )
        if item in RESTRICTION_LABELS
    ]

    include = _expand_food_terms(
        _as_list(raw.get("include_foods") or raw.get("want"))
    )
    exclude = _expand_food_terms(
        _as_list(raw.get("exclude_foods") or raw.get("avoid"))
    )

    # 模型可能把“不吃牛肉”放进 restrictions；不认识的标签尝试转成排除词
    for item in _as_list(raw.get("dietary_restrictions") or raw.get("restrictions")):
        if item in RESTRICTION_LABELS:
            continue
        if any(key in item for key in ("不吃", "不要", "别", "过敏")):
            exclude.extend(_expand_food_terms([item]))

    # 对话里明确“不吃/过敏”的标准说法，补成硬禁忌
    for phrase, label in RESTRICTION_ALIASES.items():
        if phrase in text and label not in restrictions:
            restrictions.append(label)

    include = [item for item in _unique(include) if item not in exclude][:5]

    result = {
        "taste_preferences": _unique(tastes),
        "dietary_restrictions": _unique(restrictions),
        "include_foods": include,
        "exclude_foods": _unique(exclude)[:5],
        "summary": "",
    }
    result, changed = _apply_latest_wins(result, text)

    summary = str(raw.get("summary") or "").strip()[:40]
    if changed or not summary:
        summary = _compose_summary(result)
    result["summary"] = summary
    return result


def _rule_based(text: str) -> dict:
    """模型不可用/输出不合法时的兜底；只看用户说的话，不把 AI 建议当需求。"""
    user_lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("AI：") or stripped.startswith("AI:"):
            continue
        for prefix in ("用户：", "用户:"):
            if stripped.startswith(prefix):
                stripped = stripped[len(prefix):].strip()
                break
        if stripped:
            user_lines.append(stripped)
    user_text = "\n".join(user_lines)

    tastes: list[str] = []
    if "清淡" in user_text:
        tastes.append("清淡")
    if any(word in user_text for word in ("重口", "香辣", "麻辣", "微辣", "辣")):
        if not any(word in user_text for word in ("不吃辣", "不要辣", "忌辣")):
            tastes.extend(["偏辣", "偏咸", "酱香"])
    if "甜" in user_text and not any(word in user_text for word in ("低糖", "不吃甜", "少糖")):
        tastes.append("偏甜")
    if "酸" in user_text:
        tastes.append("偏酸")
    if "咸" in user_text:
        tastes.append("偏咸")

    restrictions: list[str] = []
    for phrase, label in RESTRICTION_ALIASES.items():
        if phrase in user_text and label not in restrictions:
            restrictions.append(label)

    include: list[str] = []
    for match in re.finditer(r"(?:想吃|想要|来点|多点|吃点|喜欢|同意|可以吃)([^，。；\n]{1,16})", user_text):
        clause = match.group(1)
        include.extend(term for term in FOOD_TERMS if term in clause)
    # 用户直接说“牛肉炒饭”这种短句也视为想吃（排除“不吃”上下文）
    if not include and len(user_text) <= 20 and not any(
        word in user_text for word in ("不吃", "不要", "别", "过敏")
    ):
        include.extend(term for term in FOOD_TERMS if term in user_text)

    exclude: list[str] = []
    for match in re.finditer(r"(?:不吃|不要|别放|忌口|过敏)([^，。；\n]{1,10})", user_text):
        exclude.extend(_expand_food_terms([match.group(1)]))

    summary = user_text.replace("\n", " ")[:40]
    return {
        "taste_preferences": _unique(tastes),
        "dietary_restrictions": _unique(restrictions),
        "include_foods": _unique(include)[:5],
        "exclude_foods": _unique(exclude)[:5],
        "summary": summary,
    }


INTENT_SYSTEM = """你是饮食方案的约束翻译器，只输出一个 JSON 对象，不生成菜谱、不解释。
可用口味标签：清淡、咸鲜、香辣、微辣、麻辣、酸辣、偏甜、偏酸、偏咸、蒜香、孜然、酱香、烧烤、鲜香
可用禁忌标签：素食、蛋奶素、忌辣、少油、低糖、低盐、海鲜过敏、花生过敏、坚果过敏、乳糖不耐、麸质过敏、大豆过敏、不吃猪肉、清真、不吃牛肉
输出结构：
{"taste_preferences":[],"dietary_restrictions":[],"include_foods":[],"exclude_foods":[],"summary":""}
规则：
1. 已有勾选偏好是硬约束，不能推翻或删除，只能追加。
2. 用户明确说“不吃/不要/过敏”的，标准项放 dietary_restrictions，其他放 exclude_foods。
3. include_foods 只放用户明确想吃或明确同意的菜名/食材，最多 5 个，如“牛肉”“炒饭”。
4. AI 的推荐只有在用户明确同意时才放进 include_foods，不能把 AI 的猜测当用户要求。
5. “重口味/重口”映射为 偏咸、偏辣、酱香；“清淡”映射为 清淡。
6. 消息按时间从旧到新。同一维度冲突时，以最新明确表态为准，旧说法作废。
   例如“上周想吃辣，这周不想吃辣” → 不输出香辣/麻辣/酸辣，输出忌辣。
7. 过敏/疾病禁忌是安全项，不能因为“想吃”而删除；只有用户明确说“不过敏了/可以吃了”才解除。
8. summary 用 20 字以内概括当前有效需求；只输出 JSON。"""


def _build_prompt(profile_ctx: dict, note: str, chat_text: str) -> str:
    return (
        "【已有勾选/档案偏好】\n"
        f"口味：{profile_ctx.get('taste_preferences') or []}\n"
        f"禁忌：{profile_ctx.get('dietary_restrictions') or []}\n"
        f"目标：{profile_ctx.get('goal')}，体重：{profile_ctx.get('weight_kg')}kg\n"
        "【历史对话（从旧到新；AI 的话只作上下文，不要把 AI 推荐当用户要求）】\n"
        f"{chat_text or '（无）'}\n"
        "【本次补充要求（最新，冲突时以它为准）】\n"
        f"{note or '（无）'}\n"
        "请输出当前仍然有效的约束 JSON。"
    )


def extract_plan_intent(
    provider: LLMProvider,
    profile_ctx: dict,
    note: str = "",
    chat_text: str = "",
) -> dict:
    """调用本地/云端模型做一次结构化翻译；任何失败都退回规则解析。"""
    # note 是本次最新输入，放最后；历史里同维度旧说法会被最新的 note 覆盖。
    text = "\n".join(part for part in (chat_text or "", note or "") if part).strip()
    if not text:
        return {
            "taste_preferences": [],
            "dietary_restrictions": [],
            "include_foods": [],
            "exclude_foods": [],
            "summary": "",
        }

    try:
        raw_text = provider.chat([
            {"role": "system", "content": INTENT_SYSTEM},
            {"role": "user", "content": _build_prompt(profile_ctx, note, chat_text)},
        ])
        raw = extract_json(raw_text)
        if not isinstance(raw, dict):
            raise ValueError("意图 JSON 不是对象")
        intent = _normalize(raw, text)
        logger.info("对话意图翻译成功：%s", intent)
        return intent
    except Exception as exc:  # noqa: BLE001
        logger.warning("对话意图翻译失败，使用规则兜底：%s", exc)
        return _normalize(_rule_based(text), text)


def merge_plan_intent(profile_ctx: dict, intent: dict) -> dict:
    """把对话意图合并进档案上下文；勾选禁忌保留，对话按最新表态覆盖旧口味。"""
    merged = dict(profile_ctx)
    profile_tastes = list(profile_ctx.get("taste_preferences") or [])
    chat_tastes = list(intent.get("taste_preferences") or [])

    # 面板没显式选口味时，档案里的旧口味只是历史值；对话一旦明确表态就以对话为准。
    # 面板显式选了口味则与对话合并（面板是本次最新操作）。
    if chat_tastes:
        if profile_ctx.get("panel_taste_explicit"):
            tastes = _unique(profile_tastes + chat_tastes)
        else:
            tastes = _unique(chat_tastes)
    else:
        tastes = _unique(profile_tastes)

    merged["dietary_restrictions"] = _unique(
        list(profile_ctx.get("dietary_restrictions") or [])
        + list(intent.get("dietary_restrictions") or [])
    )
    # 最新一句“不想吃辣/忌辣”必须能覆盖档案里上周的“香辣”。
    if "忌辣" in merged["dietary_restrictions"]:
        tastes = [taste for taste in tastes if taste not in SPICY_TASTES]

    merged["taste_preferences"] = tastes
    merged["include_foods"] = list(intent.get("include_foods") or [])
    merged["exclude_foods"] = list(intent.get("exclude_foods") or [])
    if intent.get("summary"):
        merged["intent_summary"] = str(intent["summary"])[:40]
    return merged
