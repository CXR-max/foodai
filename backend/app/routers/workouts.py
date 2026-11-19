"""运动记录路由：跑步/快走/骑行（GPS）+ 手动补录

与现有体系的联动（关键设计）：
保存一条运动记录时，同时写入/完成当天的 exercise 打卡任务，这样
仪表盘的「今日消耗」「今日运动已打卡」、数据追踪的运动曲线、连续打卡、成就
全部自动生效，**不需要改动 stats / checkins 的统计逻辑**。

积分规则沿用现有两条：
- 当天有方案运动任务未完成 → 完成它（task_exercise +3，可能触发全清 +10）
- 没有方案任务 → 记为日常运动（manual_checkin 每日首次 +2，之后只记录不给分）
"""
import logging
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.db.session import get_db
from app.models.check_in import CheckIn
from app.models.health_profile import HealthProfile
from app.models.user import User
from app.models.workout import Workout
from app.schemas.workout import ManualWorkoutIn, WorkoutIn
from app.services import points, workout_calc
from app.services.achievements import calc_streak, unlock_all
from app.services.met import personalized_burn

logger = logging.getLogger("workouts")

router = APIRouter(prefix="/workouts", tags=["运动记录"])

# 这些类型走 GPS；其余（跑步机/跳绳）走手动补录
GPS_TYPES = ("跑步", "快走", "骑行")


def _naive_local(dt: datetime | None) -> datetime | None:
    """把带时区的时间转成本地时间（去掉 tzinfo）

    前端用 toISOString() 提交的是 UTC（带 Z），而库里存的是本地 naive 时间。
    不转换的话，"今天 10:23 的跑步"会显示成 02:23，还会串到前一天去打卡。
    """
    if dt is None:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt


def _profile(db: Session, user_id: int) -> dict:
    p = db.query(HealthProfile).filter(HealthProfile.user_id == user_id).first()
    if not p:
        return {}
    return {c: getattr(p, c) for c in
            ("gender", "age", "weight_kg", "height_cm", "body_fat_pct", "activity_level")}


def _workout_dict(w: Workout, with_track: bool = False) -> dict:
    d = {
        "id": w.id,
        "sport_type": w.sport_type,
        "source": w.source,
        "start_at": w.start_at.isoformat() if w.start_at else None,
        "end_at": w.end_at.isoformat() if w.end_at else None,
        "duration_sec": w.duration_sec,
        "distance_m": w.distance_m,
        "avg_pace_sec_km": w.avg_pace_sec_km,
        "best_pace_sec_km": w.best_pace_sec_km,
        "calories": w.calories,
        "intensity": w.intensity,
        "met_used": w.met_used,
        "avg_hr": w.avg_hr,
        "max_hr": w.max_hr,
        "splits": w.splits or [],
        "burn_detail": w.burn_detail,
        "note": w.note,
        "has_track": bool(w.track),
        "checkin_id": w.checkin_id,
        "created_at": w.created_at.isoformat() if w.created_at else None,
    }
    if with_track:
        d["track"] = w.track or []
        d["hr_series"] = w.hr_series or []
        d["track_stats"] = _track_stats(w.track or [])
    return d


def _track_stats(track: list[dict]) -> dict:
    """轨迹质量指标：报告页用来说清楚“这条记录有多可信”"""
    if not track:
        return {"point_count": 0, "avg_accuracy": None, "max_gap_sec": None}
    accs = [float(p.get("acc")) for p in track if p.get("acc") is not None]
    gaps = [float(track[i].get("t", 0)) - float(track[i - 1].get("t", 0))
            for i in range(1, len(track))]
    return {
        "point_count": len(track),
        "avg_accuracy": round(sum(accs) / len(accs), 1) if accs else None,
        "max_gap_sec": round(max(gaps), 1) if gaps else None,
    }


def _compare_dict(db: Session, w: Workout) -> dict | None:
    """和上一次同类型运动对比（模块内对比，不依赖社区）"""
    prev = (db.query(Workout)
            .filter(Workout.user_id == w.user_id,
                    Workout.sport_type == w.sport_type,
                    Workout.id != w.id,
                    Workout.start_at < w.start_at)
            .order_by(Workout.start_at.desc())
            .first())
    if not prev:
        return None
    return {
        "id": prev.id,
        "start_at": prev.start_at.isoformat() if prev.start_at else None,
        "distance_m": prev.distance_m,
        "duration_sec": prev.duration_sec,
        "avg_pace_sec_km": prev.avg_pace_sec_km,
        "calories": prev.calories,
        "diff_distance_m": round((w.distance_m or 0) - (prev.distance_m or 0), 1),
        "diff_duration_sec": (w.duration_sec or 0) - (prev.duration_sec or 0),
        "diff_pace_sec_km": (round(w.avg_pace_sec_km - prev.avg_pace_sec_km, 1)
                             if w.avg_pace_sec_km and prev.avg_pace_sec_km else None),
    }


def _sync_checkin(db: Session, user: User, day: date, sport_type: str,
                  duration_min: int, calories: float, intensity: str,
                  met: float | None, burn_detail: dict | None) -> tuple[CheckIn, int]:
    """把运动记录同步成当天的打卡任务（返回 打卡行 + 本次实得积分）

    只有"今天"的运动才给积分并联动打卡；补录历史只存记录，避免刷分。
    """
    is_today = day == date.today()

    # 1) 当天有方案导入的待完成运动任务 → 直接完成它
    c = None
    if is_today:
        c = (db.query(CheckIn)
             .filter(CheckIn.user_id == user.id, CheckIn.check_date == day,
                     CheckIn.check_type == "exercise", CheckIn.status == "pending")
             .order_by(CheckIn.created_at.asc()).first())

    created = False
    if c is None:
        # 2) 没有待完成任务：新建一条"运动记录"打卡（仪表盘/统计都读这张表）
        c = CheckIn(user_id=user.id, check_date=day, check_type="exercise",
                    task_key="workout", status="done",
                    exercise_type=sport_type, duration_min=duration_min,
                    calories_burned=calories, intensity=intensity,
                    met_used=met, burn_detail=(burn_detail or {}) | {"source": "运动记录自动同步"})
        db.add(c)
        created = True
    else:
        c.status = "done"
        c.exercise_type = sport_type
        c.duration_min = duration_min
        c.calories_burned = calories
        c.intensity = intensity
        c.met_used = met
        c.burn_detail = (burn_detail or {}) | {"source": "运动记录自动同步"}
        c.points_awarded = 0
    db.flush()

    pts = 0
    if is_today:
        if created:
            # 日常运动：每日首次 +2（同一天再跑只记录不给分）
            pts = points.award(db, user, "manual_checkin", once_per_day=True,
                               description=f"运动记录：{sport_type} {duration_min} 分钟",
                               ref_type="exercise") or 0
        else:
            pts = points.award(db, user, "task_exercise",
                               description=f"完成方案运动任务：{sport_type}",
                               ref_type="checkin", ref_id=c.id) or 0
            # 当天任务全部完成 → 额外 +10（每天一次）
            remaining = (db.query(CheckIn)
                         .filter(CheckIn.user_id == user.id, CheckIn.check_date == day,
                                 CheckIn.status == "pending").count())
            if remaining == 0:
                pts += points.award(db, user, "task_all_done_bonus",
                                    description=f"{day} 方案任务全部完成 🎉",
                                    once_per_day=True) or 0
        c.points_awarded = pts
        unlock_all(db, user)
    return c, pts


@router.post("")
def create_workout(
    data: WorkoutIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """保存一次运动（结束后一次性上报轨迹，服务端重算距离/配速/消耗）"""
    # 幂等：网络超时/用户连点保存时，同一个 client_id 只落一条
    if data.client_id:
        existed = (db.query(Workout)
                   .filter(Workout.user_id == user.id, Workout.client_id == data.client_id)
                   .first())
        if existed:
            return {
                **_workout_dict(existed, with_track=True),
                "points_awarded": 0,
                "message": "已保存（重复提交已忽略）",
            }

    pts_in = [p.model_dump() for p in data.track]
    kept = workout_calc.filter_points(pts_in)
    stats = workout_calc.compute_track(kept)

    # 有轨迹时，时长也以轨迹的最后一个 t 为准；前端传来的 duration 只做兜底
    if kept:
        duration_sec = int(round(float(kept[-1].get("t") or 0)))
    else:
        duration_sec = int(data.duration_sec or 0)
    # 有轨迹 → 以轨迹重算为准；无轨迹（手环/手动）→ 采信上报值
    distance_m = stats["distance_m"] if kept else float(data.distance_m or 0)

    if duration_sec <= 0:
        raise HTTPException(status_code=400, detail="运动时长无效，请重新记录")
    if not kept and distance_m <= 0 and data.sport_type in GPS_TYPES:
        raise HTTPException(status_code=400, detail="没有采集到有效轨迹，请检查定位权限后重试")

    intensity = workout_calc.estimate_intensity(data.sport_type, distance_m, duration_sec)
    met_type = workout_calc.MET_TYPE_MAP.get(data.sport_type, data.sport_type)
    duration_min = max(1, round(duration_sec / 60))
    burn = personalized_burn(met_type, duration_min, _profile(db, user.id), intensity)
    burn_detail = {"base": burn["base"], "factors": burn["factors"], "steps": burn["steps"],
                   "low": burn["low"], "high": burn["high"], "formula": burn["formula"]}

    avg_pace = (duration_sec / (distance_m / 1000.0)) if distance_m > 0 else None
    start_at = _naive_local(data.start_at) or datetime.now()
    end_at = _naive_local(data.end_at) or (start_at + timedelta(seconds=duration_sec))
    w = Workout(
        user_id=user.id,
        sport_type=data.sport_type,
        source=data.source,
        start_at=start_at,
        end_at=end_at,
        duration_sec=duration_sec,
        distance_m=round(distance_m, 1),
        avg_pace_sec_km=round(avg_pace, 1) if avg_pace else None,
        best_pace_sec_km=stats["best_pace_sec_km"],
        calories=burn["calories_burned"],
        intensity=intensity,
        met_used=burn["met"],
        burn_detail=burn_detail,
        avg_hr=data.avg_hr,
        max_hr=data.max_hr,
        hr_series=data.hr_series,
        track=kept if kept else None,
        splits=stats["splits"],
        note=data.note,
        plan_day_id=data.plan_day_id,
        client_id=data.client_id,
    )
    db.add(w)
    db.flush()

    c, pts = _sync_checkin(db, user, start_at.date(), data.sport_type,
                           duration_min, burn["calories_burned"], intensity,
                           burn["met"], burn_detail)
    w.checkin_id = c.id if c else None
    db.commit()
    db.refresh(w)

    return {
        **_workout_dict(w, with_track=True),
        "points_awarded": pts,
        "message": ("运动已保存" + (f"，+{pts} 分" if pts else "（今日运动积分已领过）")),
    }


@router.post("/manual")
def create_manual_workout(
    data: ManualWorkoutIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """手动补录（跑步机 / 跳绳 / 忘带手机）：无轨迹，只记时长与距离"""
    if data.client_id:
        existed = (db.query(Workout)
                   .filter(Workout.user_id == user.id, Workout.client_id == data.client_id)
                   .first())
        if existed:
            return {**_workout_dict(existed, with_track=True), "points_awarded": 0,
                    "message": "已保存（重复提交已忽略）"}
    start = _naive_local(data.start_at) or datetime.now()
    intensity = data.intensity or workout_calc.estimate_intensity(
        data.sport_type, data.distance_m or 0, data.duration_sec)
    met_type = workout_calc.MET_TYPE_MAP.get(data.sport_type, data.sport_type)
    duration_min = max(1, round(data.duration_sec / 60))
    burn = personalized_burn(met_type, duration_min, _profile(db, user.id), intensity)
    burn_detail = {"base": burn["base"], "factors": burn["factors"], "steps": burn["steps"],
                   "low": burn["low"], "high": burn["high"], "formula": burn["formula"],
                   "source": "手动补录"}

    avg_pace = (data.duration_sec / (data.distance_m / 1000.0)) if data.distance_m else None
    w = Workout(
        user_id=user.id, sport_type=data.sport_type, source="manual",
        start_at=start, end_at=start + timedelta(seconds=data.duration_sec),
        duration_sec=data.duration_sec,
        distance_m=round(data.distance_m, 1) if data.distance_m else 0.0,
        avg_pace_sec_km=round(avg_pace, 1) if avg_pace else None,
        calories=burn["calories_burned"], intensity=intensity, met_used=burn["met"],
        burn_detail=burn_detail, note=data.note, plan_day_id=data.plan_day_id,
        client_id=data.client_id,
    )
    db.add(w)
    db.flush()
    c, pts = _sync_checkin(db, user, start.date(), data.sport_type, duration_min,
                           burn["calories_burned"], intensity, burn["met"], burn_detail)
    w.checkin_id = c.id if c else None
    db.commit()
    db.refresh(w)

    return {**_workout_dict(w, with_track=True), "points_awarded": pts,
            "message": "已记录" + (f"，+{pts} 分" if pts else "")}


@router.get("")
def list_workouts(
    range_: str = Query(default="week", alias="range", pattern="^(week|month|all)$"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """运动记录列表（不返回轨迹点，详情页单独取，省流量）"""
    today = date.today()
    q = db.query(Workout).filter(Workout.user_id == user.id)
    if range_ == "week":
        start = datetime.combine(today - timedelta(days=today.weekday()), datetime.min.time())
        q = q.filter(Workout.start_at >= start)
    elif range_ == "month":
        start = datetime.combine(today.replace(day=1), datetime.min.time())
        q = q.filter(Workout.start_at >= start)

    total = q.count()
    rows = q.order_by(Workout.start_at.desc()).offset(offset).limit(limit).all()
    return {"total": total, "items": [_workout_dict(w) for w in rows]}


@router.get("/stats")
def workout_stats(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """运动统计：本周 / 本月 / 累计 + 近 7 天里程（首页顶部四格和趋势用）"""
    today = date.today()
    week_start = datetime.combine(today - timedelta(days=today.weekday()), datetime.min.time())
    month_start = datetime.combine(today.replace(day=1), datetime.min.time())

    def agg(start: datetime | None) -> dict:
        q = db.query(
            func.count(Workout.id),
            func.coalesce(func.sum(Workout.distance_m), 0.0),
            func.coalesce(func.sum(Workout.duration_sec), 0),
            func.coalesce(func.sum(Workout.calories), 0.0),
        ).filter(Workout.user_id == user.id)
        if start:
            q = q.filter(Workout.start_at >= start)
        cnt, dist, dur, cal = q.one()
        return {"count": cnt or 0, "distance_m": round(dist or 0, 1),
                "duration_sec": int(dur or 0), "calories": round(cal or 0, 1)}

    # 近 7 天每日里程
    week_ago = datetime.combine(today - timedelta(days=6), datetime.min.time())
    rows = (db.query(func.date(Workout.start_at), func.sum(Workout.distance_m))
            .filter(Workout.user_id == user.id, Workout.start_at >= week_ago)
            .group_by(func.date(Workout.start_at)).all())
    by_date = {str(d): round(v or 0, 1) for d, v in rows}
    daily = [{"date": str(today - timedelta(days=6 - i)),
              "distance_m": by_date.get(str(today - timedelta(days=6 - i)), 0)}
             for i in range(7)]

    best = (db.query(func.min(Workout.avg_pace_sec_km))
            .filter(Workout.user_id == user.id, Workout.avg_pace_sec_km.isnot(None),
                    Workout.avg_pace_sec_km > 0).scalar())

    return {
        "week": agg(week_start),
        "month": agg(month_start),
        "total": agg(None),
        "best_pace_sec_km": round(best, 1) if best else None,
        "daily": daily,
        "streak_days": calc_streak(db, user.id),
    }


@router.get("/{workout_id}")
def get_workout(
    workout_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """单次运动详情（含轨迹与分段），即报告页数据"""
    w = db.get(Workout, workout_id)
    if not w or w.user_id != user.id:
        raise HTTPException(status_code=404, detail="运动记录不存在")
    d = _workout_dict(w, with_track=True)
    d["compare"] = _compare_dict(db, w)
    return d


@router.delete("/{workout_id}")
def delete_workout(
    workout_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """删除运动记录；若是本条记录自动生成的打卡，一并删除以免仪表盘消耗虚高"""
    w = db.get(Workout, workout_id)
    if not w or w.user_id != user.id:
        raise HTTPException(status_code=404, detail="运动记录不存在")
    if w.checkin_id:
        c = db.get(CheckIn, w.checkin_id)
        if c and c.task_key == "workout":
            # 运动记录自己生成的打卡：直接删掉，避免仪表盘消耗虚高
            db.delete(c)
        elif c:
            # 这条运动当时抵扣的是方案里的运动任务：删除记录后任务恢复为待完成，
            # 消耗清零（积分已发，留痕不回收）
            c.status = "pending"
            c.calories_burned = 0
            c.intensity = None
            c.met_used = None
            c.burn_detail = None
            c.note = ((c.note or "") + "（运动记录已删除，任务恢复待完成）")[:300]
    db.delete(w)
    db.commit()
    return {"ok": True, "message": "已删除"}
