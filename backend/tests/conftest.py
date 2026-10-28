"""pytest 配置。

并发压测 test_leaderboard_concurrency.py 已经补了 user_id fixture，可以被 pytest
正常收集；Redis 不可用时它会自动跳过 ZSet 断言（走 SQLite 降级链路）。
本文件保留给后续 pytest 钩子使用。
"""
