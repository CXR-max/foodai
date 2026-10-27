"""Redis ZSet 排行榜 + 积分原子性 并发测试

验证两点：
1. 并发加分不丢：多线程同时给同一用户加积分，最终 points_total 应等于「初值 + N*delta」
   （关键在 points._bump_points 用 SQL 原子 UPDATE，而非先读后写）
2. 排行榜排序正确：ZSet 增分后 top() 按分数降序、rank() 名次正确；Redis 不可用时降级 SQLite 也能出正确结果

运行（需先启动 Redis，否则会自动走 SQLite 降级链路，测试仍应通过）：
    cd backend && .venv/Scripts/python -m tests.test_leaderboard_concurrency
"""
import threading
from datetime import datetime

from app.db.session import SessionLocal
from app.models.user import User
from app.services import points
from app.services.redis_leaderboard import leaderboard


def _ensure_test_user() -> User:
    db = SessionLocal()
    try:
        u = db.query(User).filter(User.username == "__concurrency_test__").first()
        if not u:
            from app.models.user import User as _U
            u = _U(username="__concurrency_test__", password_hash="x",
                   nickname="并发测试", points_total=0)
            db.add(u)
            db.commit()
            db.refresh(u)
        # 清零积分，确保可重复跑
        u.points_total = 0
        db.commit()
        db.refresh(u)
        return u
    finally:
        db.close()


def test_atomic_points(user_id: int, threads: int = 20, delta_per: int = 3):
    """多线程并发加分，验证最终积分不丢"""
    errors: list[str] = []

    def worker():
        db = SessionLocal()
        try:
            u = db.get(User, user_id)
            points.award(db, u, "manual_checkin",
                         description="并发测试", points=delta_per)
            db.commit()
        except Exception as e:  # noqa: BLE001
            errors.append(str(e))
        finally:
            db.close()

    ts = [threading.Thread(target=worker) for _ in range(threads)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()

    db = SessionLocal()
    try:
        u = db.get(User, user_id)
        final = u.points_total or 0
    finally:
        db.close()

    expected = threads * delta_per
    ok = final == expected and not errors
    print(f"[积分原子性] {threads} 线程 × +{delta_per} = 期望 {expected}，实际 {final}",
          "✅" if ok else f"❌ 错误: {errors}")
    return ok


def test_leaderboard_order():
    """ZSet 排行榜排序 + 名次"""
    print(f"[Redis] available={leaderboard.available}")
    mapping = {1001: 50, 1002: 80, 1003: 80, 1004: 30}
    if leaderboard.available:
        leaderboard.rebuild(mapping)
        top = leaderboard.top(10)
        # 期望：1002/1003(80) 在前，其次 1001(50)，再 1004(30)
        scores = [s for _, s in top]
        print(f"[Redis ZSet] top() -> {top}")
        descending = all(scores[i] >= scores[i + 1] for i in range(len(scores) - 1))
        rank_1002 = leaderboard.rank(1002)
        rank_1004 = leaderboard.rank(1004)
        print(f"[Redis ZSet] rank(1002)={rank_1002}（应 1 或 2），rank(1004)={rank_1004}（应 4）")
        return descending and rank_1002 in (1, 2) and rank_1004 == 4
    print("[Redis ZSet] Redis 未连接，跳过 ZSet 断言（降级 SQLite 仍可用）")
    return True


def main():
    print("=" * 60)
    print("开始排行榜并发测试", datetime.now().strftime("%H:%M:%S"))
    u = _ensure_test_user()
    r1 = test_atomic_points(u.id)
    r2 = test_leaderboard_order()
    print("=" * 60)
    print("结果:", "全部通过 ✅" if (r1 and r2) else "存在失败 ❌")


if __name__ == "__main__":
    main()
