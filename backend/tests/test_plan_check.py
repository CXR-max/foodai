"""方案内打卡判定：菜品对不对 / 份量对不对 / 一餐一账

三条线各一条主线，对应三种真实会出的事：

1. 菜品对不上却放行 → 用户拍的炸鸡被记成方案内，方案里的鸡胸沙拉被标"已吃"，
   第二天归因成「方案排错了」，系统用自己的错误去背锅，再让用户少吃一顿。
2. 同一餐拍两次 → 摄入翻倍，本来够的变成超标。
3. 菜品对、份量差太多 → 放过不处置，用户今天真超了却没人告诉他；
   或者反过来判成「方案排错」，让他以为方案有问题。
"""
from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.db.session import Base, get_db
from app.main import app
from app.models.check_in import CheckIn
from app.models.diet_deviation import DietDeviation
from app.models.diet_plan import DietPlan, PlanDay
from app.models.food_record import FoodRecord
from app.models.recognition import Recognition
from app.services import diet_intake, disposition, metric_bands, photo_intake

TARGETS = {
    "calorie_target": 1900, "calorie_min": 1800, "calorie_max": 2100,
    "protein_min_g": 110, "protein_max_g": 150,
    "fat_min_g": 42, "fat_max_g": 63, "carb_min_g": 214, "carb_max_g": 261,
    "fiber_min_g": 25, "sodium_max_mg": 2000, "sugar_max_g": 25,
    "water_min_ml": 2000, "sleep_min_h": 7.0, "goal": "lose",
}

MEALS = [
    {"meal_type": "lunch", "name": "午餐", "items": [
        {"name": "米饭", "portion_g": 150, "category": "主食"},
        {"name": "鸡胸肉", "portion_g": 150, "category": "蛋白"},
        {"name": "西兰花", "portion_g": 150, "category": "蔬菜"},
        {"name": "苹果", "portion_g": 180, "category": "水果"},
    ]},
    {"meal_type": "dinner", "name": "晚餐", "items": [
        {"name": "米饭", "portion_g": 200, "category": "主食"},
        {"name": "清炒时蔬", "portion_g": 200, "category": "蔬菜"},
    ]},
]


@pytest.fixture(scope="module")
def session_factory():
    engine = create_engine("sqlite+pysqlite:///:memory:",
                           connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    yield TestingSession
    app.dependency_overrides.clear()


@pytest.fixture(scope="module")
def client(session_factory):
    return TestClient(app)


@pytest.fixture(scope="module")
def auth(client):
    resp = client.post("/api/auth/register", json={
        "username": "plan_check_user", "password": "check123456", "nickname": "打卡判定",
    })
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['token']}"}


@pytest.fixture(autouse=True)
def _clean(session_factory):
    db = session_factory()
    try:
        for model in (CheckIn, FoodRecord, Recognition, PlanDay, DietPlan, DietDeviation):
            db.query(model).delete()
        db.commit()
    finally:
        db.close()
    yield


def seed_day(session_factory) -> dict:
    db = session_factory()
    try:
        plan = DietPlan(user_id=1, title="判定测试方案", start_date=date.today(),
                        status="active", summary="测试", daily_target_calories=1900,
                        targets=TARGETS, provider="test", plan_version=1)
        db.add(plan)
        db.flush()
        day = PlanDay(plan_id=plan.id, day_index=1, date=date.today(),
                      meals=[dict(m) for m in MEALS], targets=dict(TARGETS))
        db.add(day)
        db.flush()
        ids = {}
        for key in ("meal_lunch", "meal_dinner"):
            c = CheckIn(user_id=1, check_date=date.today(), check_type="diet",
                        task_key=key, status="pending", plan_day_id=day.id)
            db.add(c)
            db.flush()
            ids[key] = c.id
        db.commit()
        return {"plan_day_id": day.id, **ids}
    finally:
        db.close()


def make_recognition(session_factory, dishes: list[dict]) -> int:
    db = session_factory()
    try:
        total = round(sum(float((d.get("nutrition") or {}).get("calories") or 0) for d in dishes), 1)
        rec = Recognition(user_id=1, image_path="test.jpg", provider="test",
                          dish_name=dishes[0]["dish_name"], calories=total,
                          confidence=0.9, is_food=True,
                          result={"dishes": dishes, "nutrition": {"calories": total},
                                  "vision": {"dish_name": dishes[0]["dish_name"],
                                             "portion_g": dishes[0].get("portion_g", 200)}})
        db.add(rec)
        db.commit()
        db.refresh(rec)
        return rec.id
    finally:
        db.close()


def dish(name: str, portion_g: float, calories: float, category: str = "主食") -> dict:
    return {"dish_name": name, "portion_g": portion_g, "category": category,
            "nutrition": {"calories": calories, "protein_g": calories / 30,
                          "fat_g": calories / 90, "carb_g": calories / 5},
            "match": {"needs_confirm": False}}


# ---------------- 菜品判定四档 ----------------

def test_judge_ok_when_all_dishes_hit(session_factory):
    seed_day(session_factory)
    db = session_factory()
    try:
        judge = photo_intake.judge_plan_check(db, 1, date.today(), "lunch", [
            dish("米饭", 150, 232), dish("鸡胸肉", 150, 200),
            dish("西兰花", 150, 50, "蔬菜"), dish("苹果", 180, 95, "水果"),
        ])
    finally:
        db.close()
    assert judge["status"] == "ok"
    assert judge["matched_count"] == 4 and judge["plan_count"] == 4
    assert judge["portion"]["significant"] is False   # 克数一致，不算份量问题


def test_judge_fails_when_nothing_matches(session_factory):
    """一道都对不上 → 打卡失败，并且要说清照片里是什么、方案排的是什么"""
    seed_day(session_factory)
    db = session_factory()
    try:
        judge = photo_intake.judge_plan_check(db, 1, date.today(), "lunch", [
            dish("炸鸡", 200, 520), dish("可乐", 330, 140, "饮料")])
    finally:
        db.close()
    assert judge["status"] == "fail"
    assert judge["code"] == "dish_mismatch"
    assert judge["ok"] is False
    assert "炸鸡" in judge["reason"] and "鸡胸肉" in judge["reason"]
    # 一道都对不上就没有「仍按方案打卡」这条出口：那不是"没拍全"，是另一顿饭
    assert "force" not in {o["key"] for o in judge["options"]}
    assert {o["key"] for o in judge["options"]} >= {"extra", "change_meal", "inspect"}


def test_judge_partial_when_less_than_half(session_factory):
    """命中 1/4 判 partial：多半是没拍全，要求用户确认而不是直接放过"""
    seed_day(session_factory)
    db = session_factory()
    try:
        judge = photo_intake.judge_plan_check(db, 1, date.today(), "lunch",
                                              [dish("米饭", 150, 232)])
    finally:
        db.close()
    assert judge["status"] == "partial"
    assert judge["match_ratio"] == 0.25
    assert judge["ok"] is False            # 需要确认才算打卡成功
    # 两个都是正常路径：缺的那几道算没吃 / 按方案补上
    assert {"missing_skip", "missing_plan"} <= {o["key"] for o in judge["options"]}


def test_judge_fails_when_meal_has_no_plan(session_factory):
    """方案这天没排这餐 → 不能打卡，也不是「菜品不符」"""
    seed_day(session_factory)
    db = session_factory()
    try:
        judge = photo_intake.judge_plan_check(db, 1, date.today(), "breakfast",
                                              [dish("包子", 100, 230)])
    finally:
        db.close()
    assert judge["status"] == "fail"
    assert judge["code"] == "meal_not_planned"
    assert "force" not in {o["key"] for o in judge["options"]}   # 没菜可打卡，强行没意义


def test_judge_suggests_other_meal_when_mismatch(session_factory):
    """选错餐次是最常见的误操作：晚餐的菜选成午餐，要能指出来"""
    seed_day(session_factory)
    db = session_factory()
    try:
        judge = photo_intake.judge_plan_check(
            db, 1, date.today(), "lunch", [dish("清炒时蔬", 200, 80, "蔬菜")])
    finally:
        db.close()
    assert judge["status"] == "fail"
    assert judge["suggest_meal"]["meal_type"] == "dinner"
    change = [o for o in judge["options"] if o["key"] == "change_meal"][0]
    assert "晚餐" in change["desc"]


# ---------------- 保存闸门 ----------------

def test_mismatch_save_is_rejected_without_side_effects(session_factory, client, auth):
    """菜品不符：不落记录、不发分、不置完成——一个副作用都不能有"""
    seed_day(session_factory)
    rec_id = make_recognition(session_factory, [dish("炸鸡", 200, 520)])

    resp = client.post(f"/api/recognitions/{rec_id}/save", headers=auth,
                       json={"meal_type": "lunch", "usage": "plan"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["saved"] is False
    assert body["code"] == "dish_mismatch"
    assert body["options"]

    db = session_factory()
    try:
        assert db.query(FoodRecord).count() == 0
        task = db.query(CheckIn).filter(CheckIn.task_key == "meal_lunch").first()
        assert task.status == "pending"
        assert db.get(Recognition, rec_id).food_record_id is None
    finally:
        db.close()


def test_force_plan_is_rejected_even_if_asked(session_factory, client, auth):
    """传 force_plan 也没用：菜品对不上就是打卡失败，没有「强制」这条路

    放行等于用炸鸡的热量冒充方案那份，方案菜被标已吃，
    第二天归因成「方案排错了」，系统拿自己的错去罚用户少吃。
    不锁死用户：任务保持 pending，他仍可手动点完成（手动打卡按方案记账）。
    """
    seed_day(session_factory)
    rec_id = make_recognition(session_factory, [dish("炸鸡", 200, 520)])

    resp = client.post(f"/api/recognitions/{rec_id}/save", headers=auth,
                       json={"meal_type": "lunch", "usage": "plan", "force_plan": True})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["saved"] is False and body["code"] == "dish_mismatch"

    db = session_factory()
    try:
        assert db.query(FoodRecord).count() == 0
        assert db.query(CheckIn).filter(CheckIn.task_key == "meal_lunch").first().status == "pending"
    finally:
        db.close()


def test_failed_photo_keeps_manual_checkin_available(session_factory, client, auth):
    """打卡失败不等于这餐作废：手动点「已完成」仍然可用（按方案记账）"""
    ids = seed_day(session_factory)
    rec_id = make_recognition(session_factory, [dish("炸鸡", 200, 520)])
    client.post(f"/api/recognitions/{rec_id}/save", headers=auth,
                json={"meal_type": "lunch", "usage": "plan"})

    done = client.post(f"/api/check-ins/{ids['meal_lunch']}/complete", headers=auth)
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "done"


def test_partial_match_needs_confirm_but_can_save(session_factory, client, auth):
    """部分命中：默认不拦（用户自己点了保存），但要能拿到 partial 状态"""
    seed_day(session_factory)
    rec_id = make_recognition(session_factory, [dish("米饭", 150, 232)])

    resp = client.post(f"/api/recognitions/{rec_id}/save", headers=auth,
                       json={"meal_type": "lunch", "usage": "plan"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["plan_check"]["status"] == "partial"


def test_plan_check_endpoint_recomputes_per_meal(session_factory, client, auth):
    """切餐次必须重算：用午餐的结论去打卡晚餐，结果一定是错的"""
    seed_day(session_factory)
    rec_id = make_recognition(session_factory, [dish("清炒时蔬", 200, 80, "蔬菜")])

    lunch = client.get(f"/api/recognitions/{rec_id}/plan-check?meal_type=lunch",
                       headers=auth).json()
    dinner = client.get(f"/api/recognitions/{rec_id}/plan-check?meal_type=dinner",
                        headers=auth).json()
    assert lunch["status"] == "fail"
    assert dinner["matched_count"] == 1


# ---------------- 一餐一账 ----------------

def test_second_photo_replaces_first(session_factory, client, auth):
    """同一餐拍两次：旧的作废，摄入不翻倍"""
    seed_day(session_factory)
    first = make_recognition(session_factory, [dish("米饭", 150, 232), dish("鸡胸肉", 150, 200)])
    r1 = client.post(f"/api/recognitions/{first}/save", headers=auth,
                     json={"meal_type": "lunch", "usage": "plan"})
    assert r1.status_code == 200 and r1.json()["saved"] is True

    db = session_factory()
    try:
        before = diet_intake.day_intake(db, 1, date.today())
    finally:
        db.close()

    second = make_recognition(session_factory, [dish("米饭", 150, 232), dish("鸡胸肉", 150, 200)])
    r2 = client.post(f"/api/recognitions/{second}/save", headers=auth,
                     json={"meal_type": "lunch", "usage": "plan"})
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["voided_ids"], "第二次打卡应该作废第一次的记录"
    assert "替换" in body["message"]

    db = session_factory()
    try:
        after = diet_intake.day_intake(db, 1, date.today())
        active = db.query(FoodRecord).filter(FoodRecord.voided_at.is_(None)).count()
        voided = db.query(FoodRecord).filter(FoodRecord.voided_at.isnot(None)).count()
    finally:
        db.close()
    assert active == 2 and voided == 2
    # 摄入不能翻倍（差异只来自营养库重算的取整）
    assert abs(after["calories"] - before["calories"]) < 5


def test_manual_checkin_does_not_double_award(session_factory, client, auth):
    """先手动打卡、再拍照：不重复发任务分，文案要说「之前已经打过」"""
    ids = seed_day(session_factory)
    done = client.post(f"/api/check-ins/{ids['meal_lunch']}/complete", headers=auth)
    assert done.status_code == 200, done.text

    rec_id = make_recognition(session_factory, [dish("米饭", 150, 232), dish("鸡胸肉", 150, 200)])
    resp = client.post(f"/api/recognitions/{rec_id}/save", headers=auth,
                       json={"meal_type": "lunch", "usage": "plan"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["meal_checked"]["state"] == "already_done"
    assert body["meal_checked"]["points_awarded"] == 0
    assert "之前已经打过" in body["message"]


def test_manual_food_record_kept_unless_replace_requested(session_factory, client, auth):
    """这餐的手动记录默认不动（可能是单独记的一杯牛奶），显式要求才替换"""
    seed_day(session_factory)
    db = session_factory()
    try:
        db.add(FoodRecord(user_id=1, source="manual", dish_name="牛奶", portion_g=250,
                          calories=160, meal_type="lunch", eaten_date=date.today(),
                          eaten_at=datetime.now()))
        db.commit()
    finally:
        db.close()

    rec_id = make_recognition(session_factory, [dish("米饭", 150, 232), dish("鸡胸肉", 150, 200)])
    resp = client.post(f"/api/recognitions/{rec_id}/save", headers=auth,
                       json={"meal_type": "lunch", "usage": "plan"})
    assert resp.status_code == 200
    assert resp.json()["voided_ids"] == []

    db = session_factory()
    try:
        assert db.query(FoodRecord).filter(FoodRecord.voided_at.is_(None)).count() == 3
    finally:
        db.close()


def test_replace_manual_when_requested(session_factory, client, auth):
    seed_day(session_factory)
    db = session_factory()
    try:
        db.add(FoodRecord(user_id=1, source="manual", dish_name="牛奶", portion_g=250,
                          calories=160, meal_type="lunch", eaten_date=date.today(),
                          eaten_at=datetime.now()))
        db.commit()
    finally:
        db.close()

    rec_id = make_recognition(session_factory, [dish("米饭", 150, 232), dish("鸡胸肉", 150, 200)])
    resp = client.post(f"/api/recognitions/{rec_id}/save", headers=auth,
                       json={"meal_type": "lunch", "usage": "plan", "replace_manual": True})
    assert resp.status_code == 200
    assert len(resp.json()["voided_ids"]) == 1

    db = session_factory()
    try:
        active = db.query(FoodRecord).filter(FoodRecord.voided_at.is_(None)).all()
        assert len(active) == 2 and all(r.origin == "plan" for r in active)
    finally:
        db.close()


# ---------------- 克数修正与份量留痕 ----------------

def test_overrides_recalculate_nutrition_and_store_plan_portion(session_factory, client, auth):
    """用户把 220 g 改成 150 g：营养按新克数算，方案克重要落库"""
    seed_day(session_factory)
    rec_id = make_recognition(session_factory, [dish("米饭", 220, 340), dish("鸡胸肉", 150, 200)])

    resp = client.post(f"/api/recognitions/{rec_id}/save", headers=auth,
                       json={"meal_type": "lunch", "usage": "plan",
                             "overrides": [{"index": 0, "portion_g": 150}]})
    assert resp.status_code == 200, resp.text

    db = session_factory()
    try:
        rice = (db.query(FoodRecord)
                .filter(FoodRecord.dish_name == "米饭", FoodRecord.voided_at.is_(None)).first())
    finally:
        db.close()
    assert rice.portion_g == 150
    assert rice.plan_portion_g == 150          # 方案该菜克重
    assert rice.portion_delta_g == 0           # 改完就对齐了
    assert rice.portion_source == "photo_corrected"


def test_portion_delta_recorded_for_bigger_serving(session_factory, client, auth):
    seed_day(session_factory)
    rec_id = make_recognition(session_factory, [dish("米饭", 260, 400), dish("鸡胸肉", 150, 200)])
    resp = client.post(f"/api/recognitions/{rec_id}/save", headers=auth,
                       json={"meal_type": "lunch", "usage": "plan"})
    assert resp.status_code == 200

    db = session_factory()
    try:
        rice = db.query(FoodRecord).filter(FoodRecord.dish_name == "米饭").first()
        intake = diet_intake.day_intake(db, 1, date.today())
    finally:
        db.close()
    assert rice.plan_portion_g == 150
    assert rice.portion_delta_g == 110
    # 方案基线只有「有记录的那几餐」，不能拿全天方案来比
    assert intake["plan_baseline"]["calories"] > 0
    assert intake["plan"]["calories"] > intake["plan_baseline"]["calories"]


# ---------------- 份量越界的归因与处置 ----------------

def _intake(plan_kcal: float, baseline_kcal: float, extra_kcal: float = 0.0) -> dict:
    def block(kcal: float) -> dict:
        # 按正常供能比例铺开（蛋白 22% / 脂肪 26% / 碳水 52%），
        # 否则一个假的碳水值就会先触发碳水超标，把要测的热量归因盖住
        return {"calories": kcal,
                "protein_g": round(kcal * 0.055, 1), "fat_g": round(kcal * 0.029, 1),
                "carb_g": round(kcal * 0.126, 1),
                "fiber_g": 8, "sodium_mg": 600, "sugar_g": 5}

    total = plan_kcal + extra_kcal
    return {
        "records": 3, **block(total),
        "plan": block(plan_kcal),
        "plan_baseline": block(baseline_kcal),
        "extra": block(extra_kcal) if extra_kcal else {},
        "extra_records": 1 if extra_kcal else 0,
    }


def test_portion_over_is_attributed_to_portion_not_plan():
    """菜品对、份量吃多 → 归因「份量问题」，不能算成方案排错"""
    intake = _intake(plan_kcal=2320, baseline_kcal=1930)   # 份量多 390 kcal
    diet = metric_bands.diet_summary(TARGETS, intake=intake, pending_meals=0)
    assert diet["status"] == "calorie_over"
    assert diet["attribution"] == "portion"
    assert diet["portion"]["delta_kcal"] == 390
    assert diet["portion"]["significant"] is True
    assert "份量" in diet["hint"]
    assert "方案排错" in diet["hint"]


def test_small_portion_diff_is_not_a_portion_problem():
    """差 80 kcal 不算份量问题：估算误差本来就有这么大"""
    intake = _intake(plan_kcal=840, baseline_kcal=760)
    diet = metric_bands.diet_summary(TARGETS, intake=intake, pending_meals=0)
    assert diet["portion"]["significant"] is False
    assert diet["attribution"] != "portion"


def test_extra_snack_beats_portion_when_it_contributes_more():
    """加餐贡献大于份量偏离 → 主因是加餐，不能说成「你盛多了」"""
    intake = _intake(plan_kcal=1700, baseline_kcal=1650, extra_kcal=450)
    diet = metric_bands.diet_summary(TARGETS, intake=intake, pending_meals=0)
    assert diet["status"] == "calorie_over"
    assert diet["attribution"] == "extra"


def test_plan_over_still_blames_plan_only_when_baseline_is_out():
    """方案基线本身超上限 → 仍然归因方案（用户没责任），不被份量逻辑盖掉"""
    bad = dict(TARGETS, calorie_max=1500)
    intake = _intake(plan_kcal=1900, baseline_kcal=1850)
    diet = metric_bands.diet_summary(bad, intake=intake, pending_meals=0)
    assert diet["status"] == "plan_over"
    assert diet["attribution"] == "plan"


def test_portion_under_attributed_to_portion(session_factory):
    """吃得比方案少很多 → 份量不足，先问是没吃完还是方案量大"""
    intake = _intake(plan_kcal=900, baseline_kcal=1600)
    diet = metric_bands.diet_summary(TARGETS, intake=intake, pending_meals=0)
    assert diet["status"] == "calorie_under"
    assert diet["attribution"] == "portion"
    assert diet["portion"]["direction"] == "under"
    assert "少" in diet["hint"]


def test_disposition_gives_portion_advice_and_caps_trim(session_factory):
    """份量超标：先给「照克数盛」，且缩餐有 S6 上限（不把一餐超的全砸到下一餐）"""
    seed_day(session_factory)
    db = session_factory()
    try:
        db.add(FoodRecord(user_id=1, source="photo", origin="plan", dish_name="米饭",
                          portion_g=260, plan_portion_g=150, portion_delta_g=110,
                          calories=400, meal_type="lunch", eaten_date=date.today(),
                          eaten_at=datetime.now()))
        dev = DietDeviation(user_id=1, dev_date=date.today(), nutrient="calorie",
                            kind="over", actual=2200, limit_value=2100, delta=380,
                            attribution="portion", source="portion",
                            severity="hard", settlement="evening", status="open")
        db.add(dev)
        db.commit()
        opts = disposition.options(db, 1, date.today(), dev, TARGETS,
                                   now=datetime.combine(date.today(), datetime.min.time()).replace(hour=19))
    finally:
        db.close()

    kinds = [o["kind"] for o in opts]
    assert "portion_advice" in kinds
    advice = [o for o in opts if o["kind"] == "portion_advice"][0]
    assert "按方案克数盛" in advice["label"]

    trim = [o for o in opts if o["kind"] == "trim_remaining"]
    if trim:
        # S6：缩的不超过还没吃那餐的 15%
        assert trim[0]["payload"]["trim_kcal"] <= 650


def test_severity_marks_widely_deviated_portion_for_confirm(session_factory):
    """偏离 40% 以上：处置前先确认，否则识别错克数就成了「系统拿错数据定罪」"""
    seed_day(session_factory)
    db = session_factory()
    try:
        judge = photo_intake.judge_plan_check(db, 1, date.today(), "lunch",
                                              [dish("米饭", 300, 464), dish("鸡胸肉", 150, 200),
                                               dish("西兰花", 150, 50, "蔬菜"),
                                               dish("苹果", 180, 95, "水果")])
    finally:
        db.close()
    assert judge["portion"]["significant"] is True
    assert judge["portion"]["needs_confirm"] is True


# ---------------- 幂等与「没拍到 != 没吃」 ----------------

def test_complete_checkin_is_idempotent_after_photo(session_factory, client, auth):
    """拍照自动打卡后，用户再点「已完成」不该报错——他会以为点错了"""
    ids = seed_day(session_factory)
    rec_id = make_recognition(session_factory, [dish("米饭", 150, 232), dish("鸡胸肉", 150, 200),
                                                dish("西兰花", 150, 50, "蔬菜"),
                                                dish("苹果", 180, 95, "水果")])
    assert client.post(f"/api/recognitions/{rec_id}/save", headers=auth,
                       json={"meal_type": "lunch", "usage": "plan"}).status_code == 200

    again = client.post(f"/api/check-ins/{ids['meal_lunch']}/complete", headers=auth)
    assert again.status_code == 200, again.text
    assert again.json()["already_done"] is True
    assert again.json()["points_awarded"] == 0


def test_partial_photo_does_not_report_under(session_factory):
    """照片只拍到一道菜：不能因此说「这餐少吃 228 kcal」，没拍到不等于没吃"""
    seed_day(session_factory)
    db = session_factory()
    try:
        judge = photo_intake.judge_plan_check(db, 1, date.today(), "lunch",
                                              [dish("米饭", 150, 232)])
    finally:
        db.close()
    assert judge["status"] == "partial"
    assert judge["portion"]["complete"] is False
    assert judge["portion"]["significant"] is False      # 只按对上的那道比，量一致
    # 差只来自测试用的热值和营养库的取整，远不到「份量问题」的门槛
    assert abs(judge["portion"]["kcal_diff"]) < photo_intake.PORTION_DEV_KCAL


def test_mismatched_photo_reports_no_portion_delta(session_factory):
    """一道都对不上：没有可比对象，不能报「少吃 420 kcal」"""
    seed_day(session_factory)
    db = session_factory()
    try:
        judge = photo_intake.judge_plan_check(db, 1, date.today(), "lunch",
                                              [dish("冰淇淋", 150, 200)])
    finally:
        db.close()
    assert judge["status"] == "fail"
    assert judge["portion"]["significant"] is False


# ---------------- partial：缺的那几道算不算吃过 ----------------

def test_partial_skip_records_photo_only(session_factory, client, auth):
    """「这几道没吃」→ 只落照片里的菜，当天摄入真的偏少（文案必须说清）"""
    seed_day(session_factory)
    rec_id = make_recognition(session_factory, [dish("米饭", 150, 232)])

    resp = client.post(f"/api/recognitions/{rec_id}/save", headers=auth,
                       json={"meal_type": "lunch", "usage": "plan", "missing": "skip"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["saved"] is True
    assert body["missing"] == "skip" and body["filled_items"] == []
    assert "偏少" in body["message"]

    db = session_factory()
    try:
        rows = db.query(FoodRecord).all()
        assert len(rows) == 1 and rows[0].plan_portion_g == 150
    finally:
        db.close()


def test_partial_plan_fills_missing_items(session_factory, client, auth):
    """「都吃了，只是没拍到」→ 缺的按方案克重补记，这餐摄入等于方案"""
    seed_day(session_factory)
    rec_id = make_recognition(session_factory, [dish("米饭", 150, 232)])

    resp = client.post(f"/api/recognitions/{rec_id}/save", headers=auth,
                       json={"meal_type": "lunch", "usage": "plan", "missing": "plan"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["saved"] is True
    assert len(body["food_record_ids"]) == 4
    assert set(body["filled_items"]) == {"鸡胸肉", "西兰花", "苹果"}

    db = session_factory()
    try:
        fills = db.query(FoodRecord).filter(FoodRecord.portion_source == "plan_fill").all()
        assert len(fills) == 3
        assert all(f.portion_delta_g == 0 and f.origin == "plan" for f in fills)
        # 补的是方案那份的量，不是照片里估的量
        assert {f.dish_name: f.portion_g for f in fills}["鸡胸肉"] == 150
    finally:
        db.close()

    plan = (body["intake"].get("plan") or {}).get("calories") or 0
    base = (body["intake"].get("plan_baseline") or {}).get("calories") or 0
    assert base > 0 and abs(plan - base) < 80
