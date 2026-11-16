"""登录/注册失败限流（进程内滑动窗口）

目的：拦住撞库与批量注册。按「动作 + 客户端 IP + 用户名」计数，
窗口内失败达到阈值后直接 429，成功一次即清零。

局限（写在代码里，避免以后误以为它是分布式方案）：
- 计数存在本进程内存里，**多实例部署时各自计数**，保护力度按实例数摊薄；
- 进程重启即清零。
需要跨实例强一致时，把 _hits 换成 Redis 的 INCR + EXPIRE 即可，接口保持不变。
"""
import time
from threading import Lock

# 5 分钟内失败 5 次，锁 10 分钟
WINDOW_SECONDS = 300
MAX_FAILURES = 5
LOCK_SECONDS = 600

_lock = Lock()
_failures: dict[str, list[float]] = {}   # key -> [失败时间戳]
_locked_until: dict[str, float] = {}     # key -> 解锁时间戳


def client_key(request, scope: str, username: str = "") -> str:
    """限流键：IP + 用户名（登录时用户名不同则各自计数，避免一人被锁牵连全体）"""
    ip = "unknown"
    try:
        if request is not None and request.client:
            ip = request.client.host or "unknown"
    except Exception:  # noqa: BLE001 —— 拿不到 IP 也不能让登录挂掉
        pass
    return f"{scope}:{ip}:{username.strip().lower()[:50]}"


def retry_after(key: str) -> int:
    """还需等待多少秒才能重试；0 表示未被锁"""
    now = time.time()
    with _lock:
        until = _locked_until.get(key)
        if until and until > now:
            return int(until - now) + 1
        if until:
            _locked_until.pop(key, None)
            _failures.pop(key, None)
    return 0


def record_failure(key: str) -> int:
    """记一次失败；达到阈值则上锁，返回锁定秒数（0 表示还没锁）"""
    now = time.time()
    with _lock:
        hits = [t for t in _failures.get(key, []) if now - t < WINDOW_SECONDS]
        hits.append(now)
        _failures[key] = hits
        if len(hits) >= MAX_FAILURES:
            _locked_until[key] = now + LOCK_SECONDS
            _failures.pop(key, None)
            return LOCK_SECONDS
    return 0


def reset(key: str) -> None:
    """成功一次就清零（避免正常用户输错两次被累计）"""
    with _lock:
        _failures.pop(key, None)
        _locked_until.pop(key, None)
