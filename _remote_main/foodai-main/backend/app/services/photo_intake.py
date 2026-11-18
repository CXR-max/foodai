"""拍照打卡：三种用途 + 「菜品对不对」判定 + 一餐一账去重

为什么必须分开：
一张照片本身不说明任何归属。同一盘米饭——
- 是方案里排好的午餐 → 算「方案内」，超了就是方案的问题
- 是下午自己加的 → 算「方案外」，超了是用户的选择，给出口不指责
- 只是路过拍一下看热量 → 不该进当天摄入，否则统计天天被凭空抬高
不分开，归因就永远是错的，处置也会指错方向（让用户为方案错误少吃）。

三种用途的行为差异：
| 用途 | 落饮食记录 | origin | 是否自动打卡 |
|------|-----------|--------|-------------|
| plan | 是 | plan | **是**：对应餐次任务自动置已完成（拍了照就是吃了） |
| extra | 是 | extra | 否 |
| inspect | 否 | — | 否 |

## 两条独立的判断（这是本模块的核心）

1. **菜品对不对**：决定「能不能按方案打卡」。四档：
   ok（命中 ≥ 一半）/ partial（命中了但不足一半）/ fail（一道都不对）/
   already（这餐已经打过了，走覆盖流程）。
   一道都不对就是打卡失败——不落记录、不给分、不置完成，并且要告诉用户原因。
   放行会把「用户拍的炸鸡」记成方案内，方案里的鸡胸沙拉被标已吃，
   第二天归因成「方案排错了」，等于让系统用自己的错误去背锅。

2. **份量对不对**：不参与成败判定，只影响归因。
   菜品对、克数差 200 g 也照样打卡成功（盛多了不代表这餐没吃），
   但份量偏离要落 `plan_portion_g / portion_delta_g`，
   否则结算时分不清「方案排多了」和「你盛多了」——
   前者只能改方案，后者可以让下一餐紧一点，处置方向相反。

## 一餐一账

方案内记录按 (user, eaten_date, meal_type, origin=plan) 唯一：
- 同一餐拍两次照 → 旧记录打 `voided_at` 作废，不物理删除（识别记录还要溯源）
- 该餐已有手动记录 → **默认不动**（可能是用户单独记的一杯牛奶），
  只有显式 `replace_existing=True` 才作废；前端会先问一句
- `origin=extra` 的加餐记录永远不动：那是额外的，不是这餐
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy.orm import Session

from app.models.check_in import CheckIn
from app.models.food_record import FoodRecord
from app.models.user import User
from app.services import day_context, nutrition, points
from app.services.meal_recalc import MEAL_LABELS, recalc_meals
from app.services.llm import foods_data

USAGES = ("plan", "extra", "inspect")
USAGE_LABELS = {"plan": "方案内打卡", "extra": "额外加餐", "inspect": "只看热量"}

MEAL_TYPES = ("breakfast", "lunch", "dinner", "snack")

# 命中率 ≥ 这个比例才算「就是这餐」；不足一半要求用户确认（多半是没拍全）
MATCH_OK_RATIO = 0.5
# 份量偏离的判定：单餐热量差 ≥ max(150 kcal, 方案该餐的 20%) 才算「份量不对」
PORTION_DEV_RATIO = 0.20
PORTION_DEV_KCAL = 150
# 偏离超过 40% 且用户没改克数 → 处置前先确认一句（视觉模型估克重误差常有 ±30%）
PORTION_CONFIRM_RATIO = 0.40


def _num(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _same_dish(a: str, b: str) -> bool:
    """菜名归一化比对：完全同名，或一方包含另一方（"米饭" vs "白米饭"）

    不做模糊相似度：宁可判"不是同一道"（走额外加餐口径），也不要把
    两个不同的菜合并成一条记录——那样克重和热量都错。
    """
    x = (a or "").strip().lower().replace(" ", "")
    y = (b or "").strip().lower().replace(" ", "")
    if not x or not y:
        return False
    if x == y:
        return True
    if len(x) >= 2 and len(y) >= 2 and (x in y or y in x):
        return True
    # 别名命中同一营养库条目也算同种
    fx, fy = foods_data.find_food(a or ""), foods_data.find_food(b or "")
    if fx and fy and fx.get("name") == fy.get("name"):
        return True
    return False


def plan_meals_of(db: Session, user_id: int, day: date) -> dict[str, list[dict]]:
    """当天方案每餐的菜品（含每项热量），用于和照片比对

    热量**按营养库重算**，不读方案里存的 nutrition。
    理由：和 `diet_intake.plan_meal_nutrition` 用同一套算法，「方案基线」才不会
    和「方案内折算」打架；而且方案项经常不带 nutrition 字段，
    直接读会得到 0 kcal，于是任何照片都显得"超标"，份量判定全废。
    """
    plan_day = day_context.active_plan_day(db, user_id, day)
    if not plan_day or not plan_day.meals:
        return {}
    out: dict[str, list[dict]] = {}
    for meal in recalc_meals(plan_day.meals)["meals"]:
        meal_type = (meal.get("meal_type") or "snack").strip().lower()
        items = []
        for item in meal.get("items") or []:
            name = (item.get("name") or "").strip()
            if not name:
                continue
            portion_g = _num(item.get("portion_g"))
            category = item.get("category") or ""
            values = nutrition.match_diet_nutrition(name, portion_g, category)
            items.append({
                "name": name,
                "portion_g": portion_g,
                "category": category,
                "calories": _num(values.get("calories")),
                "protein_g": _num(values.get("protein_g")),
            })
        out.setdefault(meal_type, []).extend(items)
    return out


def _compare_items(plan_items: list[dict], dishes: list[dict]) -> dict:
    """纯计算：照片里的菜 vs 某一餐的菜（不碰数据库，便于一次比对多餐）"""
    matches: list[dict] = []
    only_photo: list[dict] = []
    used: set[int] = set()

    for dish_index, dish in enumerate(dishes):
        name = dish.get("dish_name") or ""
        nut = dish.get("nutrition") or {}
        hit = None
        for idx, item in enumerate(plan_items):
            if idx in used:
                continue
            if _same_dish(name, item["name"]):
                hit = (idx, item)
                break
        if hit:
            idx, item = hit
            used.add(idx)
            photo_g = _num(dish.get("portion_g"))
            plan_g = _num(item["portion_g"])
            photo_kcal = _num(nut.get("calories"))
            plan_kcal = _num(item["calories"])
            matches.append({
                # dish_index 是「照片里第几道菜」的下标，前端改克数要靠它对回去；
                # 不能用 matches 的顺序，未命中的菜会让两者错位
                "dish_index": dish_index,
                "dish_name": name,
                "plan_dish": item["name"],
                "category": item.get("category") or "",
                "plan_portion_g": plan_g,
                "photo_portion_g": photo_g,
                "portion_delta_g": round(photo_g - plan_g, 1),
                "portion_ratio": round(photo_g / plan_g, 2) if plan_g else None,
                "plan_kcal": round(plan_kcal),
                "photo_kcal": round(photo_kcal),
                "kcal_delta": round(photo_kcal - plan_kcal),
                "same_item": True,
            })
        else:
            only_photo.append({
                "dish_index": dish_index,
                "dish_name": name, "portion_g": _num(dish.get("portion_g")),
                "kcal": round(_num(nut.get("calories"))),
            })

    only_plan = [{"dish_name": item["name"], "portion_g": item["portion_g"],
                  "kcal": round(item["calories"])}
                 for idx, item in enumerate(plan_items) if idx not in used]

    plan_kcal_total = sum(_num(i["calories"]) for i in plan_items)
    photo_kcal_total = sum(_num((d.get("nutrition") or {}).get("calories")) for d in dishes)
    return {
        "matches": matches,
        "only_photo": only_photo,
        "only_plan": only_plan,
        "plan_kcal": round(plan_kcal_total),
        "photo_kcal": round(photo_kcal_total),
        "kcal_diff": round(photo_kcal_total - plan_kcal_total),
    }


def _hint_for(compare: dict, plan_items: list[dict]) -> str:
    matches = compare["matches"]
    only_photo = compare["only_photo"]
    big_diff = [m for m in matches if abs(m["kcal_delta"]) >= 60]
    if not plan_items:
        return "今天这餐方案里没有菜，按额外加餐记更合适"
    if big_diff:
        worst = max(big_diff, key=lambda m: abs(m["kcal_delta"]))
        direction = "多" if worst["kcal_delta"] > 0 else "少"
        return (f"{worst['plan_dish']}：方案 {worst['plan_portion_g']:g} g，"
                f"你这份约 {worst['photo_portion_g']:g} g，{direction} {abs(worst['kcal_delta'])} kcal。"
                "按你实际吃的算，方案不会重复计。")
    if only_photo and not matches:
        return "照片里的菜方案里没有，建议按额外加餐记"
    return "和方案基本一致，按实际吃的算"


def compare_with_plan(db: Session, user_id: int, day: date, meal_type: str | None,
                      dishes: list[dict]) -> dict:
    """照片里的菜 vs 方案里的菜：找出同种菜的分量差异（识别阶段的展示用）"""
    plan_meals = plan_meals_of(db, user_id, day)
    plan_items = plan_meals.get((meal_type or "").strip().lower(), []) if meal_type else \
        [item for items in plan_meals.values() for item in items]
    compare = _compare_items(plan_items, dishes)
    return {
        "meal_type": meal_type,
        "meal_label": MEAL_LABELS.get(meal_type or "", ""),
        **compare,
        "has_plan_meal": bool(plan_items),
        "suggest_extra": (not plan_items) or (not compare["matches"] and bool(compare["only_photo"])),
        "hint": _hint_for(compare, plan_items),
    }


# ---------------- 份量偏离 ----------------

def portion_deviation(compare: dict, plan_items: list[dict], *,
                      complete: bool = True) -> dict:
    """这一餐比方案多吃/少吃多少，以及是否够得上「份量问题」

    只有同时满足「相对 20%」和「绝对 150 kcal」才算——小餐 20% 常常只有 80 kcal，
    为这种量级提醒是噪音。但一旦算作份量问题，它就会参与归因和处置。

    complete=False（照片没拍全）时**只比已经对上的那几道**：
    拿整餐总额比会把「没拍到」算成「少吃 200 kcal」，
    用户明明吃了、只是没拍，系统却说他不足，比不判定更糟。
    """
    matches = compare.get("matches") or []
    if complete:
        plan_kcal = _num(compare.get("plan_kcal"))
        photo_kcal = _num(compare.get("photo_kcal"))
    else:
        plan_kcal = sum(_num(m.get("plan_kcal")) for m in matches)
        photo_kcal = sum(_num(m.get("photo_kcal")) for m in matches)

    diff = photo_kcal - plan_kcal
    threshold = max(PORTION_DEV_KCAL, plan_kcal * PORTION_DEV_RATIO)
    ratio = (diff / plan_kcal) if plan_kcal else 0.0
    significant = bool(plan_kcal) and abs(diff) >= threshold

    detail = ""
    if significant:
        if diff > 0:
            biggest = None
            for m in matches:
                if m["portion_delta_g"] > 0 and (biggest is None or
                                                 m["portion_delta_g"] > biggest["portion_delta_g"]):
                    biggest = m
            extra = (f"（{biggest['plan_dish']} 多吃 {biggest['portion_delta_g']:g} g）"
                     if biggest else "")
            scope = "" if complete else "对上的这几道"
            detail = f"{scope}比方案多 {int(round(diff))} kcal{extra}"
        else:
            biggest = None
            for m in matches:
                if m["portion_delta_g"] < 0 and (biggest is None or
                                                 m["portion_delta_g"] < biggest["portion_delta_g"]):
                    biggest = m
            extra = (f"（{biggest['plan_dish']} 少了 {abs(biggest['portion_delta_g']):g} g）"
                     if biggest else "")
            scope = "" if complete else "对上的这几道"
            detail = f"{scope}比方案少 {int(round(abs(diff)))} kcal{extra}"

    return {
        "significant": significant,
        "complete": complete,
        "direction": "over" if diff > 0 else ("under" if diff < 0 else "none"),
        "plan_kcal": round(plan_kcal),
        "photo_kcal": round(photo_kcal),
        "kcal_diff": round(diff),
        "ratio": round(ratio, 3),
        # 偏离够大又没被用户改过克数 → 处置前先确认，别用错误数据定罪
        "needs_confirm": significant and abs(ratio) >= PORTION_CONFIRM_RATIO,
        "items": matches,
        "detail": detail,
    }


# ---------------- 已有记录 / 打卡现状 ----------------

def _meal_checkin(db: Session, user_id: int, day: date, meal_type: str) -> dict:
    row = (db.query(CheckIn)
           .filter(CheckIn.user_id == user_id, CheckIn.check_date == day,
                   CheckIn.task_key == f"meal_{meal_type}")
           .order_by(CheckIn.id.desc())
           .first())
    if not row:
        return {"state": "none", "checkin_id": None}
    return {"state": "done" if row.status == "done" else "pending", "checkin_id": row.id}


def meal_existing(db: Session, user_id: int, day: date, meal_type: str) -> dict:
    """这餐现在有什么：已打卡吗？已有几条记录？拍照会不会顶掉它们"""
    rows = (db.query(FoodRecord)
            .filter(FoodRecord.user_id == user_id, FoodRecord.eaten_date == day,
                    FoodRecord.meal_type == meal_type,
                    FoodRecord.voided_at.is_(None))
            .all())
    plan_recs = [r for r in rows if (r.origin or "").strip().lower() == "plan"]
    manual_recs = [r for r in rows if not (r.origin or "").strip()]
    checkin = _meal_checkin(db, user_id, day, meal_type)

    if plan_recs:
        note = f"这餐已经拍过照记了 {len(plan_recs)} 条，重新打卡会替换掉旧的"
    elif manual_recs:
        note = f"这餐有 {len(manual_recs)} 条手动记录，可以一起替换，也可以保留另算"
    elif checkin["state"] == "done":
        note = "这餐已经打过卡了，照片只用来更新摄入明细"
    else:
        note = ""
    return {
        "checked": checkin["state"] == "done",
        "checkin_state": checkin["state"],
        "checkin_id": checkin["checkin_id"],
        "plan_records": len(plan_recs),
        "manual_records": len(manual_recs),
        "will_replace_photo": bool(plan_recs),
        "will_replace_manual": False,
        "note": note,
    }


def void_meal_records(db: Session, user_id: int, day: date, meal_type: str,
                      reason: str, *, include_manual: bool = False) -> list[int]:
    """作废这餐的方案内记录（一餐一账）；include_manual 时才动手动记录

    `origin=extra` 永远不动——那是额外的加餐，不是这餐。
    """
    rows = (db.query(FoodRecord)
            .filter(FoodRecord.user_id == user_id, FoodRecord.eaten_date == day,
                    FoodRecord.meal_type == meal_type,
                    FoodRecord.voided_at.is_(None))
            .all())
    now = datetime.now()
    out: list[int] = []
    for row in rows:
        origin = (row.origin or "").strip().lower()
        if origin == "plan" or (include_manual and not origin):
            row.voided_at = now
            row.voided_reason = reason
            out.append(row.id)
    if out:
        db.flush()
    return out


def fill_plan_items(db: Session, user_id: int, day: date, meal_type: str,
                    names: list[str]) -> list[dict]:
    """照片没拍到、但用户说「吃了」的那几道 → 按**方案自己的克重**补记

    用方案值而不是照片值：方案本来就这么排的，数据可信；
    这样补完这餐摄入就等于「按方案吃完了」，和「都吃了，只是没拍到」的文案一致。
    """
    plan_items = plan_meals_of(db, user_id, day).get(meal_type) or []
    wanted = {str(n).strip() for n in (names or []) if str(n).strip()}
    out: list[dict] = []
    for item in plan_items:
        if item["name"] not in wanted:
            continue
        values = nutrition.match_diet_nutrition(item["name"], item["portion_g"],
                                                item.get("category") or "")
        out.append({
            "dish_name": item["name"],
            "portion_g": item["portion_g"],
            "category": item.get("category") or "",
            "plan_portion_g": item["portion_g"],
            "calories": _num(values.get("calories")),
            "protein_g": _num(values.get("protein_g")),
            "fat_g": _num(values.get("fat_g")),
            "carb_g": _num(values.get("carb_g")),
            "fiber_g": values.get("fiber_g"),
            "sodium_mg": values.get("sodium_mg"),
            "sugar_g": values.get("sugar_g"),
        })
    return out


# ---------------- 菜品判定 ----------------

def _best_other_meal(plan_meals: dict[str, list[dict]], dishes: list[dict],
                     current: str) -> dict | None:
    """照片内容更像哪一餐？选错餐次是这类误操作里最常见的一种"""
    best = None
    for meal_type in MEAL_TYPES:
        if meal_type == current:
            continue
        items = plan_meals.get(meal_type) or []
        if not items:
            continue
        hit = len(_compare_items(items, dishes)["matches"])
        if hit and (best is None or hit > best["hits"]):
            best = {"meal_type": meal_type, "label": MEAL_LABELS.get(meal_type, meal_type),
                    "hits": hit, "plan_count": len(items)}
    return best


def judge_plan_check(db: Session, user_id: int, day: date, meal_type: str,
                     dishes: list[dict]) -> dict:
    """菜品对不对：决定能不能按方案打卡

    这是「拍照即打卡」的闸门。放行错菜会把用户额外吃的东西记成方案内，
    第二天归因就是「方案排错了」——等于让系统用自己的错误去背锅，
    再顺手给用户一个「少吃点」的建议。所以这里宁可拒。
    """
    meal_type = (meal_type or "").strip().lower()
    plan_day = day_context.active_plan_day(db, user_id, day)
    plan_meals = plan_meals_of(db, user_id, day) if plan_day else {}
    plan_items = plan_meals.get(meal_type) or []
    compare = _compare_items(plan_items, dishes)
    matched = len(compare["matches"])
    plan_count = len(plan_items)
    # 照片没拍全时只比「已经对上的那几道」，否则没拍到的菜会被算成"少吃"
    portion = portion_deviation(compare, plan_items,
                                complete=bool(plan_count) and matched == plan_count)
    existing = meal_existing(db, user_id, day, meal_type)
    ratio = round(matched / plan_count, 3) if plan_count else 0.0

    photo_names = [d.get("dish_name") or "" for d in dishes if d.get("dish_name")]
    plan_names = [i["name"] for i in plan_items]
    label = MEAL_LABELS.get(meal_type, meal_type)
    gap_names = "、".join(i["dish_name"] for i in compare["only_plan"])

    if not plan_day or not (getattr(plan_day, "meals", None) or []):
        status, code = "fail", "no_plan_day"
        reason = "今天还没有饮食方案，没有可打卡的餐次。要先按目标生成方案。"
        options = [
            {"key": "extra", "label": "按额外加餐记", "desc": "热量照算，不影响方案打卡"},
            {"key": "inspect", "label": "只看热量", "desc": "不落记录，也不影响当天摄入"},
        ]
    elif not plan_items:
        status, code = "fail", "meal_not_planned"
        reason = f"今天{label}方案里没有菜（可能没排，或者这餐被删了），没法按方案打卡。"
        options = [
            {"key": "extra", "label": "按额外加餐记", "desc": "这餐不在方案里，记成加餐最准"},
            {"key": "change_meal", "label": "改到别的餐次", "desc": "如果其实吃的是方案里的另一餐"},
            {"key": "inspect", "label": "只看热量", "desc": "不落记录"},
        ]
    elif matched == 0:
        status, code = "fail", "dish_mismatch"
        reason = (f"照片里是{'「' + '」「'.join(photo_names) + '」' if photo_names else '没有识别到菜'}，"
                  f"{label}方案排的是「{'」「'.join(plan_names)}」，一道都对不上。"
                  "这不算方案打卡。")
        # 这里**没有**「仍按方案打卡」：一道都对不上，就不存在「照片没拍全」的解释
        # （真没拍全是 partial）。放行等于用 A 菜的热量冒充 B 菜，方案那几道被标已吃，
        # 第二天归因成「方案排错了」——系统拿自己的错去罚用户。
        # 不锁死用户：这餐任务保持 pending，他仍可手动点完成（手动打卡按方案记账）。
        options = [
            {"key": "extra", "label": "按额外加餐记",
             "desc": "照片里这些是方案外的，这样记热量照算，也不冤枉方案"},
            {"key": "change_meal", "label": "改到别的餐次",
             "desc": "可能是餐次选错了，换一餐重新比对"},
            {"key": "inspect", "label": "只看热量", "desc": "不落记录"},
        ]
    elif ratio < MATCH_OK_RATIO:
        status, code = "partial", "partial_match"
        reason = (f"照片里对上了 {matched}/{plan_count} 道"
                  f"（{'、'.join(m['plan_dish'] for m in compare['matches'])}），"
                  f"还差「{gap_names}」——是没拍到，还是这几道没吃？")
        # 两个都是正常路径，数据各自自洽：
        # skip：只按照片记 → 当天摄入真的偏少（文案要说清）
        # plan：缺的几道按方案克重补记 → 这餐等于方案（用的是方案自己的值，不是照片值）
        options = [
            {"key": "missing_skip", "label": "这几道没吃",
             "desc": f"只按照片记，缺的「{gap_names}」不算，今天热量会偏少"},
            {"key": "missing_plan", "label": "都吃了，只是没拍到",
             "desc": f"「{gap_names}」按方案克重补上，这餐和方案一致"},
            {"key": "extra", "label": "改成额外加餐", "desc": "如果拍照的其实不是方案这餐"},
            {"key": "change_meal", "label": "改到别的餐次", "desc": "换一餐重新比对"},
        ]
    else:
        status, code = "ok", "matched"
        reason = f"对上了方案{label}的 {matched}/{plan_count} 道菜"
        options = []

    # 已有记录/已打卡：不是失败，但要告知会不会覆盖
    if status in ("ok", "partial") and existing["plan_records"]:
        existing["note"] = (f"这餐已经拍过照记了 {existing['plan_records']} 条，"
                            "提交后会替换掉旧的，不会叠加")
    if status in ("ok", "partial") and existing["checked"]:
        existing["note"] = existing["note"] or "这餐已经打过卡，照片只更新摄入明细，不再重复给分"

    # 建议改餐次：当前对不上、但别的餐对得上
    suggest_meal = None
    if status == "fail" and dishes:
        suggest_meal = _best_other_meal(plan_meals, dishes, meal_type)
        if suggest_meal:
            for opt in options:
                if opt["key"] == "change_meal":
                    opt["desc"] = (f"这些菜更像{suggest_meal['label']}，"
                                   f"换过去能对上 {suggest_meal['hits']} 道")

    return {
        "ok": status == "ok",
        "status": status,
        "code": code,
        "meal_type": meal_type,
        "meal_label": label,
        "reason": reason,
        "matched_count": matched,
        "plan_count": plan_count,
        "match_ratio": ratio,
        "matches": compare["matches"],
        "only_photo": compare["only_photo"],
        "only_plan": compare["only_plan"],
        "plan_kcal": compare["plan_kcal"],
        "photo_kcal": compare["photo_kcal"],
        "portion": portion,
        "existing": existing,
        "options": options,
        # 不再有 can_force：菜品对不上就打卡不成功，没有"强制"这条路。
        # partial 只剩「缺的那几道算不算吃过」这一个选择，默认按没吃记（更保守）
        "missing_default": "skip",
        "suggest_meal": suggest_meal,
        "hint": _hint_for(compare, plan_items),
    }


def suggest_meal_type(now: datetime | None = None) -> str:
    """按时间猜这餐属于哪一顿（用户可改，只是默认值别让人每次手选）"""
    now = now or datetime.now()
    hour = now.hour
    if hour < 10:
        return "breakfast"
    if hour < 15:
        return "lunch"
    if hour < 21:
        return "dinner"
    return "snack"


def mark_meal_done(db: Session, user: User, day: date, meal_type: str) -> dict:
    """拍照打卡 → 对应餐次任务自动置「已完成」

    返回 state：done（这次置的）/ already_done（之前就打过）/ no_task（这天没这个任务）。
    已打过不能报成「没任务」——用户会以为系统丢了任务，实际是他自己点过了。
    """
    task_key = f"meal_{meal_type}"
    label = MEAL_LABELS.get(meal_type, meal_type)
    row = (db.query(CheckIn)
           .filter(CheckIn.user_id == user.id, CheckIn.check_date == day,
                   CheckIn.task_key == task_key)
           .order_by(CheckIn.id.desc())
           .first())
    if not row:
        return {"state": "no_task", "task_key": task_key, "label": label,
                "points_awarded": 0}
    if row.status == "done":
        return {"state": "already_done", "checkin_id": row.id, "task_key": task_key,
                "label": label, "points_awarded": 0}

    row.status = "done"
    row.note = ((row.note or "") + "；拍照打卡").strip("；")
    pts = points.award(db, user, "task_meal",
                       description=f"拍照完成方案任务：{label}",
                       ref_type="checkin", ref_id=row.id) or 0
    row.points_awarded = pts
    db.flush()
    return {"state": "done", "checkin_id": row.id, "task_key": task_key,
            "label": label, "points_awarded": pts}


def resolve_usage(db: Session, user_id: int, dishes: list[dict], meal_type: str | None,
                  day: date) -> dict:
    """识别阶段给出「用途建议」：同种菜命中方案 → 建议 plan；都没命中 → 建议 extra"""
    compare = compare_with_plan(db, user_id, day, meal_type, dishes)
    suggested = "extra" if compare["suggest_extra"] else "plan"
    return {
        "suggested_usage": suggested,
        "suggested_label": USAGE_LABELS[suggested],
        "plan_compare": compare,
        "meal_type": meal_type or suggest_meal_type(),
    }


def attach_origin(records: list[FoodRecord], usage: str) -> None:
    """给刚落库的饮食记录打归属标记"""
    origin = "extra" if usage == "extra" else "plan"
    for record in records:
        record.origin = origin
