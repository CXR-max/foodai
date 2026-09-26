"""
比赛演示造数脚本：创建几个排行用户 + 历史积分，让排行榜不那么空
用法：cd backend && python seed_demo.py
（不会覆盖已有用户；重复执行安全）
"""
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from app.core.security import hash_password
from app.db.session import SessionLocal
from app.models.points_log import PointsLog
from app.models.user import User

DEMO_USERS = [
    ("demo_xiaoming", "小明", 620),
    ("demo_xiaohong", "小红", 480),
    ("demo_fitness", "健身达人Leo", 355),
    ("demo_yoga", "瑜伽酱", 210),
    ("demo_newbie", "养生新人", 95),
]


def main():
    db = SessionLocal()
    try:
        created = 0
        for username, nickname, total in DEMO_USERS:
            if db.query(User).filter(User.username == username).first():
                continue
            user = User(username=username, password_hash=hash_password("demo123456"),
                        nickname=nickname, points_total=total)
            db.add(user)
            db.flush()
            # 造几条积分流水（让流水页不空）
            actions = ["完成方案任务：午餐", "完成方案任务：运动", "生成养生方案", "完善健康档案"]
            points_vals = [3, 3, 10, 20]
            remain, i = total, 0
            while remain > 0 and i < 40:
                p = points_vals[i % 4] if remain >= 10 else remain
                db.add(PointsLog(
                    user_id=user.id, action="task_meal",
                    points=p, description=random.choice(actions),
                    created_at=datetime.now() - timedelta(days=random.randint(0, 13), hours=random.randint(0, 12)),
                ))
                remain -= p
                i += 1
            created += 1
        db.commit()
        print(f"[演示数据] 新建 {created} 个排行用户（密码均为 demo123456）")
    finally:
        db.close()


if __name__ == "__main__":
    main()
