"""当日模式（例外场景）：聚餐 / 训练 / 生理期 / 生病 / 出差

为什么例外要单独一层，而不是塞进判定函数或用户档案：
- 塞进判定函数 → 口径被特例污染，谁都说不清"这条规则对谁生效"
- 塞进用户档案 → 聚餐只有一天，改成档案后忘了改回来，之后永久放宽
- **按天存**才对：它只影响当天这 24 小时的判定口径，第二天自动恢复

三类影响（effects）：
1. `calorie_max_pct`：放宽热量上限。聚餐吃超是常态，但**放宽也有天花板**——
   最多放宽到维持热量（TDEE），绝不放到「随便吃」。增重比一顿饭的负罪感更糟。
2. `relaxed_keys`：这些项当天判为 soft（提示但不记 hard 偏离），不阻塞、不进连续统计。
   聚餐→钠/糖/脂肪；出差→纤维/钠；生病→蛋白下限。
3. `pause_scoring` / `no_disposition`：当天不评等级、不推处置（生病/生理期）。
   人生病卧床时被系统催"你还差 300 kcal"，是产品设计失职。

normal（没有记录）走默认口径，与不加模式完全一致——这条保证「不填任何东西也不影响原有判定」。
"""
from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.models.diet_deviation import DayMode

MODE_LABELS = {
    "normal": "正常",
    "social": "聚餐",
    "training": "加练",
    "period": "生理期",
    "sick": "生病",
    "travel": "出差",
}

# 用户可主动选择的模式（normal 用 clear 表达，不出现在选择列表里）
SELECTABLE_MODES = ("social", "training", "period", "sick", "travel")

# 每种模式的口径调整。数值理由写在 note 里，前端直接展示给用户看。
MODE_SPECS: dict[str, dict] = {
    "normal": {
        "calorie_max_pct": 0.0,
        "relaxed_keys": [],
        "pause_scoring": False,
        "no_disposition": False,
        "note": "",
    },
    "social": {
        # 一顿饭吃到 +15% 很常见；再往上就不是"聚餐"而是"暴食"了，不纵容
        "calorie_max_pct": 0.15,
        "relaxed_keys": ["sodium", "sugar", "fat"],
        "pause_scoring": False,
        "no_disposition": False,
        # 聚餐的钠糖脂肪必然超，那不是自控问题；热量仍看，因为热量是唯一能自己控的量
        "note": "聚餐日：热量上限放宽 15%（不超过维持热量），钠/糖/脂肪只提示不记偏离",
    },
    "training": {
        # 加练日多消耗的要部分回补，否则掉肌肉；与 effective_calorie_band 的 40% 同源
        "calorie_max_pct": 0.10,
        "relaxed_keys": ["sodium"],
        "pause_scoring": False,
        "no_disposition": False,
        "note": "加练日：热量上限放宽 10%，蛋白下限照旧（练了更该吃够蛋白）",
    },
    "period": {
        "calorie_max_pct": 0.08,
        "relaxed_keys": ["sugar", "fat"],
        "pause_scoring": True,
        "no_disposition": True,
        # 生理期食欲上升、体重波动都是生理性的，这时候压摄入既不安全也没用
        "note": "生理期：不评等级、不推处置，热量上限放宽 8%，想吃甜的正常，别自责",
    },
    "sick": {
        "calorie_max_pct": 0.10,
        "relaxed_keys": ["protein", "fiber", "sodium", "sugar", "fat"],
        "pause_scoring": True,
        "no_disposition": True,
        # 生病时唯一重要的是吃下去和喝水，营养素配比先放一边
        "note": "生病期：只看吃没吃、喝够水，营养素配比不判定",
    },
    "travel": {
        "calorie_max_pct": 0.10,
        "relaxed_keys": ["fiber", "sodium"],
        "pause_scoring": False,
        "no_disposition": False,
        # 外食纤维必然低、钠必然高，这两项不是用户能控的
        "note": "出差：热量上限放宽 10%，纤维/钠只提示（外食难控），蛋白和热量仍看",
    },
}


def spec_for(mode: str | None) -> dict:
    return MODE_SPECS.get((mode or "normal").strip().lower(), MODE_SPECS["normal"])


def get_mode(db: Session, user_id: int, day: date) -> DayMode | None:
    return (db.query(DayMode)
            .filter(DayMode.user_id == user_id, DayMode.mode_date == day)
            .first())


def mode_name(db: Session, user_id: int, day: date) -> str:
    row = get_mode(db, user_id, day)
    return (row.mode if row else "normal") or "normal"


def effects(db: Session, user_id: int, day: date) -> dict:
    """当天生效的口径调整（没有记录 → normal 的全零口径）"""
    row = get_mode(db, user_id, day)
    mode = (row.mode if row else "normal") or "normal"
    spec = dict(spec_for(mode))
    spec.update({
        "mode": mode,
        "label": MODE_LABELS.get(mode, mode),
        "note_text": (row.note if row and row.note else spec.get("note") or ""),
        "recorded": row is not None,
    })
    return spec


def apply_to_targets(targets: dict, effect: dict, *, tdee: float | None = None) -> dict:
    """把模式放宽套到目标区间上（返回副本，不改原 targets）

    天花板：放宽后的上限不超过维持热量（TDEE）。没有 TDEE 时退化为「原上限 + 放宽量」。
    这是安全底线的一部分——例外不等于放开。
    """
    merged = dict(targets or {})
    pct = float((effect or {}).get("calorie_max_pct") or 0)
    if pct <= 0:
        return merged
    base_max = merged.get("calorie_max")
    try:
        base_max = float(base_max) if base_max else 0.0
    except (TypeError, ValueError):
        base_max = 0.0
    if base_max <= 0:
        return merged

    bumped = base_max * (1 + pct)
    ceiling = None
    if tdee and tdee > 0:
        ceiling = float(tdee)
    if ceiling:
        bumped = min(bumped, ceiling)

    bumped = int(round(bumped / 10.0) * 10)
    if bumped > base_max:
        merged["calorie_max"] = bumped
        merged["day_mode_note"] = (
            f"今天按「{effect.get('label')}」放宽：热量上限 {int(base_max)} → {bumped} kcal"
            + ("（已到维持热量，不再放宽）" if ceiling and bumped >= ceiling else "")
        )
    return merged


def set_mode(db: Session, user_id: int, day: date, mode: str, note: str | None = None) -> DayMode:
    """设置/覆盖当天模式；mode=normal 等价于清除"""
    mode = (mode or "normal").strip().lower()
    if mode not in MODE_SPECS:
        mode = "normal"
    row = get_mode(db, user_id, day)
    if mode == "normal":
        if row:
            db.delete(row)
            db.flush()
        return None

    payload = {k: v for k, v in spec_for(mode).items()}
    if row:
        row.mode = mode
        row.note = note
        row.effects = payload
    else:
        row = DayMode(user_id=user_id, mode_date=day, mode=mode, note=note, effects=payload)
        db.add(row)
    db.flush()
    return row


def mode_dict(row: DayMode | None) -> dict | None:
    if not row:
        return None
    return {
        "date": str(row.mode_date),
        "mode": row.mode,
        "label": MODE_LABELS.get(row.mode, row.mode),
        "note": row.note,
        "effects": row.effects or spec_for(row.mode),
    }
