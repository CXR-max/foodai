"""pytest 配置。

test_leaderboard_concurrency.py 是并发压测脚本（按文档用
`python -m tests.test_leaderboard_concurrency` 运行），它的函数签名带
user_id/threads 这类业务参数，不是 fixture，交给 pytest 收集会直接报错。
这里不收集它，保持单测套件干净。
"""
collect_ignore = ["test_leaderboard_concurrency.py"]
