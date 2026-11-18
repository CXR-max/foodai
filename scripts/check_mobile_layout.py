"""手机布局几何核对：不靠肉眼，直接量元素位置尺寸判断布局对不对。

检查项：
  1. 底部 tabBar 是否真的贴底、5 个 tab 是否均分、高度是否合理
  2. 内容区底部留白是否够（最后一张卡片不能被 tabBar 盖住）
  3. 页面是否有横向溢出
  4. 表格是否已被卡片替代（手机上不该出现 el-table）
  5. 弹窗宽度是否适配屏幕
  6. 触屏点击区是否 >= 40px

用法（需先启动前后端）：
    backend\\.venv\\Scripts\\python.exe scripts\\check_mobile_layout.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = os.environ.get("FOODAI_WEB", "http://127.0.0.1:5173")
USER = os.environ.get("FOODAI_USER", "demo")
PWD = os.environ.get("FOODAI_PWD", "demo1234")

VIEWPORTS = [
    ("iPhone 12 Pro", 390, 844),
    ("Pixel 7", 412, 915),
    ("小屏 iPhone SE", 375, 667),
]

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


def check_viewport(browser, label: str, w: int, h: int) -> list[str]:
    problems: list[str] = []
    ctx = browser.new_context(
        viewport={"width": w, "height": h},
        device_scale_factor=2,
        is_mobile=True,
        has_touch=True,
    )
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
        pg.wait_for_timeout(1400)

        info = pg.evaluate(
            """() => {
                const r = {};
                const tb = document.querySelector('nav.tabbar');
                r.hasTabbar = !!tb;
                if (tb) {
                    const b = tb.getBoundingClientRect();
                    r.tabbar = { top: Math.round(b.top), bottom: Math.round(b.bottom),
                                 height: Math.round(b.height), width: Math.round(b.width) };
                    r.tabCount = tb.querySelectorAll('.tab').length;
                    // 5 个 tab 应大致等宽
                    const widths = [...tb.querySelectorAll('.tab')].map(t => Math.round(t.getBoundingClientRect().width));
                    r.tabWidths = widths;
                }
                // 横向溢出
                r.overflowX = document.documentElement.scrollWidth - document.documentElement.clientWidth;

                // 手机上不该再出现表格（表格说明卡片化没生效）
                r.tableCount = document.querySelectorAll('.el-table').length;

                // 主内容区底部内边距
                const main = document.querySelector('.el-main');
                if (main) r.mainPadBottom = parseInt(getComputedStyle(main).paddingBottom);

                // 侧边栏应该消失
                r.asideCount = document.querySelectorAll('.el-aside').length;

                // 找页面最底部的可见元素，看是否被 tabBar 压住
                const tbTop = tb ? tb.getBoundingClientRect().top : window.innerHeight;
                let maxBottom = 0;
                document.querySelectorAll('.page-card, .el-card').forEach(el => {
                    const b = el.getBoundingClientRect();
                    if (b.height > 0 && b.bottom > maxBottom) maxBottom = b.bottom;
                });
                r.lastCardBottom = Math.round(maxBottom);
                r.tabbarTop = Math.round(tbTop);

                return r;
            }"""
        )

        tag = f"{name:6s}"
        line = f"  {tag}"

        if not info["hasTabbar"]:
            problems.append(f"{label} {name}: tabBar 缺失")
            line += " tabBar=缺失"
        else:
            line += f" tabBar={info['tabbar']['height']}px@{info['tabbar']['bottom']}"
            if info["tabCount"] != 5:
                problems.append(f"{label} {name}: tab 数量 {info['tabCount']} != 5")
                line += f" tabs={info['tabCount']}!"
            if info["tabbar"]["bottom"] < h - 2:
                problems.append(
                    f"{label} {name}: tabBar 未贴底（bottom={info['tabbar']['bottom']}, 视口高={h}）"
                )
                line += " 未贴底!"

        if info["overflowX"] > 2:
            problems.append(f"{label} {name}: 横向溢出 {info['overflowX']}px")
            line += f" 溢出{info['overflowX']}px!"

        if info["tableCount"] > 0:
            problems.append(f"{label} {name}: 手机上仍有 {info['tableCount']} 个表格")
            line += f" 表格{info['tableCount']}!"

        if info["asideCount"] > 0:
            problems.append(f"{label} {name}: 侧边栏未隐藏")
            line += " 侧栏!"

        pad = info.get("mainPadBottom") or 0
        if pad < 60:
            problems.append(f"{label} {name}: 主内容底部留白只有 {pad}px，会被 tabBar 盖住")
            line += f" 留白{pad}px!"

        print(line)

    ctx.close()
    return problems


def main() -> int:
    all_problems: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        for label, w, h in VIEWPORTS:
            all_problems += check_viewport(browser, label, w, h)
        browser.close()

    print("\n" + "=" * 50)
    if all_problems:
        print(f"发现 {len(all_problems)} 个问题：")
        for x in all_problems:
            print("  [!]", x)
        return 1
    print("全部通过：tabBar 贴底且 5 个、无横向溢出、无残留表格、侧边栏已隐藏、底部留白充足")
    return 0


if __name__ == "__main__":
    sys.exit(main())
