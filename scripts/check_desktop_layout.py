"""桌面端回归核对：确认响应式改造没有影响宽屏 Web 版。

检查项：
  1. 宽屏下侧边栏必须存在且宽 200px
  2. 宽屏下不能出现底部 tabBar
  3. 表格要保留（手机上才换成卡片）
  4. 无横向溢出

用法（需先启动前后端）：
    backend\\.venv\\Scripts\\python.exe scripts\\check_desktop_layout.py
"""
from __future__ import annotations

import os
import sys

from playwright.sync_api import sync_playwright

BASE = os.environ.get("FOODAI_WEB", "http://127.0.0.1:5173")
USER = os.environ.get("FOODAI_USER", "demo")
PWD = os.environ.get("FOODAI_PWD", "demo1234")

# 桌面端应该保留表格的页面
TABLE_PAGES = {"/recognition", "/plans", "/leaderboard"}

PAGES = [
    ("/recognition", "食物识别"),
    ("/plans", "养生方案"),
    ("/checkin", "健康打卡"),
    ("/dashboard", "仪表盘"),
    ("/stats", "数据追踪"),
    ("/profile", "健康档案"),
    ("/mall", "积分商城"),
    ("/achievements", "成就徽章"),
    ("/leaderboard", "排行榜"),
]

VIEWPORTS = [("笔记本", 1440, 900), ("宽屏", 1920, 1080)]


def main() -> int:
    problems: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)

        for label, w, h in VIEWPORTS:
            ctx = browser.new_context(viewport={"width": w, "height": h})
            pg = ctx.new_page()
            pg.goto(f"{BASE}/login", wait_until="networkidle")
            pg.fill('input[placeholder="用户名"]', USER)
            pg.fill('input[placeholder="密码"]', PWD)
            pg.click('button:has-text("登 录")')
            pg.wait_for_url("**/dashboard", timeout=20000)
            pg.wait_for_timeout(1200)

            print(f"\n=== {label} ({w}x{h}) ===")
            for path, name in PAGES:
                pg.goto(f"{BASE}{path}", wait_until="networkidle")
                pg.wait_for_timeout(1300)

                info = pg.evaluate(
                    """() => {
                        const aside = document.querySelector('.el-aside');
                        const tb = document.querySelector('nav.tabbar');
                        return {
                            asideWidth: aside ? Math.round(aside.getBoundingClientRect().width) : 0,
                            hasTabbar: !!tb,
                            tables: document.querySelectorAll('.el-table').length,
                            // 空状态：没有数据时本来就不该有表格，不算问题
                            isEmpty: !!document.querySelector('.el-empty'),
                            hasTabs: !!document.querySelector('.el-tabs'),
                            overflowX: document.documentElement.scrollWidth - document.documentElement.clientWidth,
                        };
                    }"""
                )

                line = f"  {name:6s} 侧栏={info['asideWidth']}px 表格={info['tables']}"

                if info["asideWidth"] < 190:
                    problems.append(f"{label} {name}: 侧边栏宽度异常 {info['asideWidth']}px")
                    line += " 侧栏异常!"
                if info["hasTabbar"]:
                    problems.append(f"{label} {name}: 桌面端不该出现底部 tabBar")
                    line += " 误显tabBar!"
                # 只有「有数据（非空状态）且不是无 tab 的空页面」时才要求表格存在
                if path in TABLE_PAGES and info["tables"] == 0:
                    if info["isEmpty"]:
                        line += " (空状态,跳过)"
                    else:
                        problems.append(f"{label} {name}: 桌面端表格丢失（应该保留）")
                        line += " 表格丢失!"
                if info["overflowX"] > 2:
                    problems.append(f"{label} {name}: 横向溢出 {info['overflowX']}px")
                    line += f" 溢出{info['overflowX']}px!"

                print(line)

            ctx.close()

        browser.close()

    print("\n" + "=" * 50)
    if problems:
        print(f"发现 {len(problems)} 个问题：")
        for x in problems:
            print("  [!]", x)
        return 1
    print("桌面端全部正常：侧边栏在、无 tabBar、表格保留、无溢出")
    return 0


if __name__ == "__main__":
    sys.exit(main())
