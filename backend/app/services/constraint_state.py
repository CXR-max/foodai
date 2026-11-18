"""长期约束状态：当前有效快照 + 旧版本审计。

规则：
- 每个用户只有一条 active 快照（superseded_at is null），每次更新插入新版本；
- taste/include/exclude 有明确新值时覆盖，没有则继承；
- hard 禁忌只增不减，除非 intent.removed_restrictions 是显式“不过敏/可吃”确认；
- “这周/今晚”等临时约束带 valid_until，过期后自动失效。
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from app.db.session import SessionLocal
from app.models.plan_constraint_state import PlanConstraintState

logger = logging.getLogger("constraint_state")


def _unique(values) -> list[str]:
    result: list[str] = []
    for value in values or []:
        value = str(value).strip()
        if value and value not in result:
            result.append(value)
    return result


def empty_data() -> dict:
    return {
        "taste_preferences": [],
        "hard_restrictions": [],
        "soft_preferences": [],
        "include_foods": [],
        "exclude_foods": [],
        "meal_rules": {},
        "removed_restrictions": [],
    }


def _split_restrictions(labels) -> tuple[list[str], list[str]]:
    from app.services.plan_intent import HARD_RESTRICTIONS, SOFT_RESTRICTIONS
    hard = [label for label in _unique(labels) if label in HARD_RESTRICTIONS]
    soft = [label for label in _unique(labels) if label in SOFT_RESTRICTIONS]
    return hard, soft


def _data_from_profile(profile) -> dict:
    data = empty_data()
    if profile is None:
        return data
    data["taste_preferences"] = _unique(profile.taste_preferences or [])
    hard, soft = _split_restrictions(profile.dietary_restrictions or [])
    data["hard_restrictions"] = hard
    data["soft_preferences"] = soft
    return data


def get_active_state(user_id: int) -> dict | None:
    db = SessionLocal()
    try:
        row = (
            db.query(PlanConstraintState)
            .filter(PlanConstraintState.user_id == user_id,
                    PlanConstraintState.superseded_at.is_(None))
            .order_by(PlanConstraintState.version.desc())
            .first()
        )
        if not row:
            return None
        now = datetime.now()
        if row.valid_until and row.valid_until < now:
            row.superseded_at = now
            db.commit()
            return None
        return {
            "id": row.id,
            "version": row.version,
            "data": dict(row.data or {}),
            "source_message_id": row.source_message_id,
            "scope": row.scope or "long_term",
            "valid_from": row.valid_from,
            "valid_until": row.valid_until,
            "confidence": row.confidence or 0.8,
            "updated_at": row.updated_at,
        }
    finally:
        db.close()


def ensure_state(user_id: int, profile=None) -> dict:
    state = get_active_state(user_id)
    if state:
        return state
    db = SessionLocal()
    try:
        now = datetime.now()
        row = PlanConstraintState(
            user_id=user_id, version=1, data=_data_from_profile(profile),
            source_message_id=None, scope="long_term",
            valid_from=now, valid_until=None, confidence=1.0, updated_at=now,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return {
            "id": row.id, "version": row.version, "data": dict(row.data or {}),
            "source_message_id": None, "scope": "long_term",
            "valid_from": now, "valid_until": None,
            "confidence": 1.0, "updated_at": now,
        }
    finally:
        db.close()


def state_for_user(state: dict | None, user_text: str) -> dict | None:
    """按用户原话修正状态：include 只保留用户明确说过的；补规则排除项。"""
    if not state or not (user_text or "").strip():
        return state
    data = dict(state.get("data") or {})
    include = [item for item in (data.get("include_foods") or []) if item in user_text]
    data["include_foods"] = include

    try:
        from app.services.plan_intent import _normalize, _rule_based
        rule = _normalize(_rule_based(user_text), user_text)
        excludes = list(rule.get("exclude_foods") or [])
        if excludes:
            data["exclude_foods"] = _unique(list(data.get("exclude_foods") or []) + excludes)
            data["include_foods"] = [
                item for item in data["include_foods"] if item not in data["exclude_foods"]
            ]
    except Exception:  # noqa: BLE001
        logger.exception("规则修正状态失败")

    copied = dict(state)
    copied["data"] = data
    return copied


def state_prompt(state: dict | None) -> str:
    if not state:
        return "（暂无长期约束状态）"
    data = state.get("data") or {}
    parts = []
    if data.get("taste_preferences"):
        parts.append("口味：" + "/".join(data["taste_preferences"]))
    if data.get("hard_restrictions"):
        parts.append("硬禁忌：" + "/".join(data["hard_restrictions"]))
    if data.get("soft_preferences"):
        parts.append("软偏好：" + "/".join(data["soft_preferences"]))
    if data.get("include_foods"):
        parts.append("想吃：" + "/".join(data["include_foods"]))
    if data.get("exclude_foods"):
        parts.append("不吃：" + "/".join(data["exclude_foods"]))
    if data.get("meal_rules"):
        parts.append("餐次规则：" + str(data["meal_rules"])[:120])
    if state.get("valid_until"):
        parts.append("有效期至：" + state["valid_until"].strftime("%Y-%m-%d %H:%M"))
    return "；".join(parts) if parts else "（暂无有效约束）"


def apply_state_to_ctx(profile_ctx: dict, state: dict | None) -> dict:
    """长期状态注入方案上下文；当前面板的显式口味优先于旧状态。"""
    if not state:
        return profile_ctx
    data = state.get("data") or {}
    ctx = dict(profile_ctx)

    if not ctx.get("panel_taste_explicit") and data.get("taste_preferences"):
        ctx["taste_preferences"] = list(data["taste_preferences"])

    restrictions = list(ctx.get("dietary_restrictions") or [])
    restrictions += list(data.get("hard_restrictions") or [])
    if not ctx.get("panel_restrictions_explicit"):
        restrictions += list(data.get("soft_preferences") or [])
    ctx["dietary_restrictions"] = _unique(restrictions)

    if data.get("include_foods"):
        ctx["include_foods"] = _unique(list(ctx.get("include_foods") or []) + data["include_foods"])
    if data.get("exclude_foods"):
        ctx["exclude_foods"] = _unique(list(ctx.get("exclude_foods") or []) + data["exclude_foods"])
    ctx["constraint_state_version"] = state.get("version", 0)
    ctx["constraint_state_summary"] = state_prompt(state)
    return ctx


def _merge_data(current: dict, intent: dict, state: dict) -> tuple[dict, bool]:
    data = {**empty_data(), **current}
    changed = False

    new_taste = _unique(intent.get("taste_preferences") or [])
    if new_taste:
        if new_taste != data.get("taste_preferences"):
            data["taste_preferences"] = new_taste
            changed = True

    hard = _unique(list(data.get("hard_restrictions") or []) + list(intent.get("hard_restrictions") or []))
    removed = _unique(intent.get("removed_restrictions") or [])
    if removed:
        before = list(hard)
        hard = [item for item in hard if item not in removed]
        changed = changed or before != hard
    if hard != data.get("hard_restrictions"):
        data["hard_restrictions"] = hard
        changed = True

    # 忌辣与香辣/麻辣/酸辣互斥：hard 忌辣时同步清掉口味里的辣标签
    if "忌辣" in hard:
        spicy = {"香辣", "微辣", "麻辣", "酸辣"}
        cleaned = [item for item in (data.get("taste_preferences") or []) if item not in spicy]
        if cleaned != data.get("taste_preferences"):
            data["taste_preferences"] = cleaned
            changed = True

    soft = _unique(list(data.get("soft_preferences") or []) + list(intent.get("soft_preferences") or []))
    if soft != data.get("soft_preferences"):
        data["soft_preferences"] = soft
        changed = True

    new_include = _unique(intent.get("include_foods") or [])
    new_exclude = _unique(intent.get("exclude_foods") or [])
    include = _unique(list(data.get("include_foods") or []) + new_include)
    exclude = _unique(list(data.get("exclude_foods") or []) + new_exclude)
    # 最新表态只覆盖同一道菜，不影响其他菜的排除/想吃
    for item in new_include:
        exclude = [value for value in exclude if value != item]
    for item in new_exclude:
        include = [value for value in include if value != item]
    if include != data.get("include_foods"):
        data["include_foods"] = include
        changed = True
    if exclude != data.get("exclude_foods"):
        data["exclude_foods"] = exclude
        changed = True

    if data.get("hard_restrictions"):
        from app.services.planner import hard_chat_terms
        bad_terms = hard_chat_terms(data["hard_restrictions"])
        cleaned = [item for item in data.get("include_foods") or []
                   if not any(term in item for term in bad_terms)]
        if cleaned != data.get("include_foods"):
            data["include_foods"] = cleaned
            changed = True

    meal_rules = intent.get("meal_rules") or {}

    meal_rules = intent.get("meal_rules") or {}
    if isinstance(meal_rules, dict) and meal_rules:
        merged_rules = {**(data.get("meal_rules") or {}), **meal_rules}
        if merged_rules != data.get("meal_rules"):
            data["meal_rules"] = merged_rules
            changed = True

    if removed:
        data["removed_restrictions"] = _unique(list(data.get("removed_restrictions") or []) + removed)

    return data, changed


def update_state(user_id: int, intent: dict, source_message_id: int | None = None,
                 profile=None, hard_restrictions: list[str] | None = None,
                 soft_preferences: list[str] | None = None) -> dict:
    intent = dict(intent)
    if hard_restrictions:
        intent["hard_restrictions"] = _unique(
            list(intent.get("hard_restrictions") or []) + hard_restrictions
        )
        intent["dietary_restrictions"] = _unique(
            list(intent.get("dietary_restrictions") or []) + hard_restrictions
        )
    if soft_preferences:
        intent["soft_preferences"] = _unique(
            list(intent.get("soft_preferences") or []) + soft_preferences
        )
        intent["dietary_restrictions"] = _unique(
            list(intent.get("dietary_restrictions") or []) + soft_preferences
        )

    current = ensure_state(user_id, profile)
    data, changed = _merge_data(current.get("data") or {}, intent, current)
    if not changed:
        return current

    now = datetime.now()
    scope = current.get("scope") or "long_term"
    valid_until = current.get("valid_until")
    if intent.get("scope_explicit"):
        scope = intent.get("scope") or "long_term"
        valid_until = intent.get("valid_until")

    db = SessionLocal()
    try:
        old = (db.query(PlanConstraintState)
               .filter(PlanConstraintState.user_id == user_id,
                       PlanConstraintState.superseded_at.is_(None))
               .order_by(PlanConstraintState.version.desc()).first())
        version = (old.version + 1) if old else 1
        if old:
            old.superseded_at = now
        row = PlanConstraintState(
            user_id=user_id, version=version, data=data,
            source_message_id=source_message_id, scope=scope,
            valid_from=now, valid_until=valid_until,
            confidence=float(intent.get("confidence") or 0.8),
            updated_at=now,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        logger.info("约束状态更新 user=%s version=%s scope=%s", user_id, version, scope)
        return {
            "id": row.id, "version": row.version, "data": dict(row.data or {}),
            "source_message_id": source_message_id, "scope": scope,
            "valid_from": now, "valid_until": valid_until,
            "confidence": row.confidence, "updated_at": now,
        }
    finally:
        db.close()


def reset_state(user_id: int) -> None:
    """清空对话时让长期状态失效，下次从档案重新初始化。"""
    db = SessionLocal()
    try:
        now = datetime.now()
        (db.query(PlanConstraintState)
         .filter(PlanConstraintState.user_id == user_id,
                 PlanConstraintState.superseded_at.is_(None))
         .update({PlanConstraintState.superseded_at: now}, synchronize_session=False))
        db.commit()
    finally:
        db.close()


def update_state_from_text(user_id: int, text: str,
                           source_message_id: int | None = None,
                           profile=None, hard_restrictions: list[str] | None = None,
                           soft_preferences: list[str] | None = None) -> dict | None:
    """聊天后轻量增量更新（规则解析，不阻塞/不额外调用大模型）。"""
    if not (text or "").strip():
        return None
    try:
        from app.services.plan_intent import _normalize, _rule_based
        intent = _normalize(_rule_based(text), text)
        return update_state(
            user_id, intent, source_message_id, profile,
            hard_restrictions=hard_restrictions, soft_preferences=soft_preferences,
        )
    except Exception:  # noqa: BLE001
        logger.exception("规则增量更新约束状态失败")
        return None
