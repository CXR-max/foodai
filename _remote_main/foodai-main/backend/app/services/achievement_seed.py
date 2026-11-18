"""成就徽章种子数据（应用启动时自动写入，想改条件/加徽章改这个列表）

condition_type:
  recognize_count        累计识别次数
  save_record_count      累计保存饮食记录次数
  streak_days            连续打卡天数
  manual_exercise_count  累计手动运动打卡次数
  points_total           累计积分
  plan_count             生成方案次数
"""
from sqlalchemy.orm import Session

from app.models.achievement import Achievement

# code, name, icon, description, category, condition_type, threshold
SEED_ACHIEVEMENTS = [
    ("first_recognize",  "初试身手", "📷", "完成第一次 AI 食物识别", "badge", "recognize_count", 1),
    ("recognize_10",     "火眼金睛", "🔍", "累计识别 10 次食物", "badge", "recognize_count", 10),
    ("recognize_50",     "鉴食大师", "📸", "累计识别 50 次食物", "badge", "recognize_count", 50),
    ("first_record",     "记录起步", "🍱", "第一次保存饮食记录", "badge", "save_record_count", 1),
    ("record_10",        "饮食管家", "🥗", "累计保存 10 条饮食记录", "badge", "save_record_count", 10),
    ("streak_3",         "三日之约", "🌱", "连续打卡 3 天", "badge", "streak_days", 3),
    ("streak_7",         "七日恒心", "🔥", "连续打卡 7 天", "badge", "streak_days", 7),
    ("streak_21",        "习惯成钢", "💎", "连续打卡 21 天", "badge", "streak_days", 21),
    ("exercise_5",       "活力满满", "🏃", "累计手动运动打卡 5 次", "badge", "manual_exercise_count", 5),
    ("first_plan",       "规划人生", "🗺️", "生成第一份 AI 养生方案", "badge", "plan_count", 1),
    ("points_100",       "百分战绩", "💯", "累计积分达到 100", "title", "points_total", 100),
    ("points_500",       "积分大亨", "👑", "累计积分达到 500", "title", "points_total", 500),
]


def seed_achievements(db: Session) -> int:
    """把成就种子写进库（按 code 去重，幂等；返回本次新增数量）"""
    existing = {a.code for a in db.query(Achievement.code).all()}
    added = 0
    for code, name, icon, desc, cat, ctype, th in SEED_ACHIEVEMENTS:
        if code in existing:
            continue
        db.add(Achievement(code=code, name=name, icon=icon, description=desc,
                           category=cat, condition_type=ctype, threshold=th))
        existing.add(code)
        added += 1
    if added:
        db.commit()
    return added
