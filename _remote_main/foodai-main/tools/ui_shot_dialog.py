# 个性化消耗弹窗截图（开发辅助工具，放在 D 盘；依赖 playwright，用完可卸载）
import os
import sys

from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, ".tmp_shots")
BASE = os.environ.get("FOODAI_WEB", "http://localhost:5173")


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    with sync_playwright() as p:
        b = p.chromium.launch(channel="msedge", headless=True)
        pg = b.new_page(viewport={"width": 1440, "height": 1100})
        pg.goto(f"{BASE}/login", wait_until="networkidle")
        pg.fill('input[placeholder="用户名"]', "demo_xiaoming")
        pg.fill('input[placeholder="密码"]', "demo123456")
        pg.click('button:has-text("登 录")')
        pg.wait_for_url("**/dashboard", timeout=20000)

        # 打卡页 → 打开运动消耗预估弹窗
        pg.click('li:has-text("健康打卡")')
        pg.wait_for_timeout(2000)
        pg.click('button:has-text("打卡")')
        pg.wait_for_timeout(1800)
        pg.screenshot(path=os.path.join(OUT, "7_burn_dialog_moderate.png"))

        # 切"吃力"档，看实时重算
        pg.click('.el-dialog label:has-text("吃力")')
        pg.wait_for_timeout(1500)
        pg.screenshot(path=os.path.join(OUT, "8_burn_dialog_intense.png"))
        pg.click('.el-dialog button:has-text("取消")')

        # 档案页：体脂率输入
        pg.click('li:has-text("健康档案")')
        pg.wait_for_timeout(2000)
        pg.screenshot(path=os.path.join(OUT, "9_profile_bodyfat.png"))
        b.close()
    print("done:", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
