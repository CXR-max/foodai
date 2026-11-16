"""pytest 配置。

并发压测 test_leaderboard_concurrency.py 已经补了 user_id fixture，可以被 pytest
正常收集；Redis 不可用时它会自动跳过 ZSet 断言（走 SQLite 降级链路）。

另外一个必做的事：清空登录/注册限流计数。
各测试模块各自 register 一个账号，全量跑时同一个测试 IP 很容易攒到 5 次触发 429，
表现为「单独跑通过、全量跑 fixture 报错」，这里统一在每条用例前清零。
"""
import pytest

from app.core import ratelimit


@pytest.fixture(autouse=True)
def _reset_ratelimit():
    ratelimit.reset_all()
    yield
    ratelimit.reset_all()
