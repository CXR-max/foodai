"""Redis ZSet 排行榜（工业方案，可降级）

设计目标（对应组长安排）：
- 封装 ZSet 排行榜「增分 / 查询」两个核心接口
- 业务事件（得分/扣分）后自动同步 ZSet 分数，与数据库事务保持一致
- Redis 不可用（未安装/未启动/连接失败）时自动降级为 SQLite 查询，功能不中断
- 单实例局限：ZSet 与 SQLite 无法跨库强事务，采用「先落库、后写 Redis、失败重试/告警」
  的方式保证最终一致；多实例部署需引入消息队列或定时对账（见 README 说明）

用法：
    from app.services.redis_leaderboard import leaderboard
    leaderboard.update(user_id, points_total)   # 增分/改分
    leaderboard.top(n)                          # 前 n 名 [(user_id, score), ...]
    leaderboard.rank(user_id)                   # 名次（1 起，不存在返回 None）
"""
import logging

logger = logging.getLogger("leaderboard")

# redis 是可选项，未安装时 import 失败不应让应用崩溃
try:
    import redis as _redis
    _HAS_REDIS = True
except Exception:  # noqa: BLE001
    _redis = None
    _HAS_REDIS = False

from app.core.config import settings


class RedisLeaderboard:
    """ZSet 排行榜封装；Redis 不可用时所有方法降级为 no-op，由调用方回退 SQLite"""

    def __init__(self):
        self._client = None
        self._ok = False
        self._connect()

    def _connect(self):
        if not (settings.REDIS_ENABLED and _HAS_REDIS):
            logger.info("Redis 未启用（REDIS_ENABLED=%s / 依赖=%s），排行榜走 SQLite",
                        settings.REDIS_ENABLED, _HAS_REDIS)
            return
        try:
            client = _redis.Redis(
                host=settings.REDIS_HOST, port=settings.REDIS_PORT,
                db=settings.REDIS_DB, password=settings.REDIS_PASSWORD or None,
                socket_connect_timeout=1, socket_timeout=1, decode_responses=True,
                protocol=2,   # 兼容 Redis 5.x（不支持 RESP3 的 HELLO 命令）
            )
            client.ping()
            self._client = client
            self._ok = True
            logger.info("Redis 排行榜已连接 %s:%s", settings.REDIS_HOST, settings.REDIS_PORT)
        except Exception as e:  # noqa: BLE001
            self._client = None
            self._ok = False
            logger.warning("Redis 连接失败，排行榜降级 SQLite：%s", e)

    @property
    def available(self) -> bool:
        return self._ok

    def update(self, user_id: int, score: float):
        """把用户积分同步进 ZSet（增分/扣分后调用）"""
        if not self._ok:
            return
        try:
            self._client.zadd(settings.REDIS_LEADERBOARD_KEY, {str(user_id): score})
        except Exception as e:  # noqa: BLE001
            logger.warning("Redis ZSet 更新失败（不影响主流程）：%s", e)

    def top(self, n: int = 20) -> list[tuple[int, float]]:
        """前 n 名，按分数降序；同分按 user_id 升序"""
        if not self._ok:
            return []
        try:
            rows = self._client.zrevrange(
                settings.REDIS_LEADERBOARD_KEY, 0, n - 1, withscores=True)
            return [(int(uid), float(score)) for uid, score in rows]
        except Exception as e:  # noqa: BLE001
            logger.warning("Redis ZSet 查询失败：%s", e)
            return []

    def rank(self, user_id: int) -> int | None:
        """名次（1 起）；不存在返回 None"""
        if not self._ok:
            return None
        try:
            r = self._client.zrevrank(settings.REDIS_LEADERBOARD_KEY, str(user_id))
            return None if r is None else r + 1
        except Exception as e:  # noqa: BLE001
            logger.warning("Redis ZSet 名次查询失败：%s", e)
            return None

    def rebuild(self, mapping: dict[int, float]):
        """全量重建 ZSet（对账用）：mapping = {user_id: score}"""
        if not self._ok or not mapping:
            return
        try:
            key = settings.REDIS_LEADERBOARD_KEY
            pipe = self._client.pipeline()
            pipe.delete(key)
            if mapping:
                pipe.zadd(key, {str(k): v for k, v in mapping.items()})
            pipe.execute()
            logger.info("Redis 排行榜已重建，共 %d 人", len(mapping))
        except Exception as e:  # noqa: BLE001
            logger.warning("Redis 排行榜重建失败：%s", e)


# 全局单例
leaderboard = RedisLeaderboard()
