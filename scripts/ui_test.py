"""
UI 端到端走查：注册 → 档案 → 识别 → 方案 → 打卡 → 统计 → 商城 → 排行榜
前提：后端(8000)和前端(5173)都已启动
运行：E:/app/miniconda3/python.exe scripts/ui_test.py
截图输出到 scripts/shots/
"""
import os
import sys
import time

from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding="utf-8")
SHOTS = os.path.join(os.path.dirname(__file__), "shots")
os.makedirs(SHOTS, exist_ok=True)

errors = []
uniqu = str(int(time.time()))
username = f"uitest_{uniqu}"


def shot(page, name):
    page.screenshot(path=os.path.join(SHOTS, f"{name}.png"), full_page=False)
    print(f"  📸 {name}.png")


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))

    # 1. 打开首页 → 跳登录页
    page.goto("http://localhost:5173")
    page.wait_for_load_state("networkidle")
    assert "/login" in page.url, f"应跳转登录页，实际 {page.url}"
    print("✓ 未登录跳转登录页")
    shot(page, "01_login")

    # 2. 注册
    page.click("text=立即注册")
    page.wait_for_selector("input[placeholder='确认密码']")   # 等注册页渲染完
    page.fill("input[placeholder='用户名']", username)
    page.fill("input[placeholder='昵称（可选，排行榜展示）']", "UI测试员")
    page.fill("input[placeholder='密码（至少6位）']", "uitest123456")
    page.fill("input[placeholder='确认密码']", "uitest123456")
    page.click("button:has-text('注 册')")
    page.wait_for_url("**/dashboard", timeout=8000)
    page.wait_for_load_state("networkidle")
    print("✓ 注册成功进入仪表盘")
    shot(page, "02_dashboard")

    # 3. 健康档案
    page.click("text=健康档案")
    page.wait_for_load_state("networkidle")
    page.locator(".el-input-number input").nth(0).fill("175")   # 身高
    page.locator(".el-input-number input").nth(1).fill("70")    # 体重
    page.locator(".el-input-number input").nth(2).fill("25")    # 年龄
    page.click("text=保存档案")
    page.wait_for_timeout(800)
    assert "22.9" in page.inner_text("body"), "BMI 应显示 22.9"
    print("✓ 档案保存，BMI=22.9")
    shot(page, "03_profile")

    # 4. 食物识别（造一张小图上传）
    import struct, zlib
    def make_png(path):
        def chunk(t, d):
            c = t + d
            return struct.pack(">I", len(d)) + c + struct.pack(">I", zlib.crc32(c))
        w = h = 64
        raw = b"".join(b"\x00" + b"\xe8\xa4\x9b" * w for _ in range(h))
        png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
               + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
        open(path, "wb").write(png)

    img = os.path.join(SHOTS, "food.png")
    make_png(img)
    page.click("text=食物识别")
    page.wait_for_load_state("networkidle")
    page.set_input_files("input[type=file]", img)
    page.wait_for_selector("text=识别结果", timeout=15000)
    page.wait_for_timeout(1500)
    body = page.inner_text("body")
    assert ("识别到" in body or "保存为饮食记录" in body or "未识别到食物" in body), "应出现识别结果"
    print("✓ 识别完成")
    shot(page, "04_recognition")
    # 保存为饮食记录
    if page.locator("button:has-text('保存为饮食记录')").count():
        page.click("button:has-text('保存为饮食记录')")
        page.wait_for_timeout(500)
        page.click(".el-dialog button:has-text('保存')")
        page.wait_for_timeout(1000)
        print("✓ 已保存为饮食记录")
    shot(page, "05_recognition_saved")

    # 5. 生成方案（mock，应较快）
    page.click("text=养生方案")
    page.wait_for_load_state("networkidle")
    page.click("button:has-text('生成 7 天方案')")
    page.wait_for_timeout(500)
    page.click("button:has-text('开始生成')")
    page.wait_for_selector(".el-tabs__item", timeout=60000)
    page.wait_for_timeout(800)
    assert page.locator(".el-tabs__item").count() >= 7, "应有 7 个天标签"
    print("✓ 方案生成（7 天 tabs）")
    shot(page, "06_plans")
    # 导入第 1 天
    page.click("button:has-text('导入日历')")
    page.wait_for_timeout(1200)
    print("✓ 第 1 天已导入日历")
    shot(page, "07_plans_imported")

    # 6. 打卡页逐项完成
    page.click("text=健康打卡")
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(800)
    shot(page, "08_checkin")
    n_before = page.locator("button:has-text('打卡 +3')").count()
    for _ in range(n_before):
        btn = page.locator("button:has-text('打卡 +3')").first
        if btn.count():
            btn.click()
            page.wait_for_timeout(700)
    print(f"✓ 完成了 {n_before} 项打卡")
    shot(page, "09_checkin_done")

    # 7. 数据追踪
    page.click("text=数据追踪")
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(1200)
    shot(page, "10_stats")

    # 8. 积分商城
    page.click("text=积分商城")
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(800)
    body = page.inner_text("body")
    assert "我的积分" in body and "自律新人头衔" in body, "商城应显示奖励列表"
    print("✓ 商城奖励加载")
    shot(page, "11_mall")

    # 9. 排行榜
    page.click("text=排行榜")
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(800)
    body = page.inner_text("body")
    assert "小明" in body and "UI测试员" in body, "排行榜应包含演示用户和我"
    print("✓ 排行榜（含演示用户与我）")
    shot(page, "12_leaderboard")

    # 10. 刷新登录态保持
    page.reload()
    page.wait_for_load_state("networkidle")
    assert "/login" not in page.url, "刷新后不应被踢回登录页"
    print("✓ 刷新后登录态保持")

    browser.close()

print()
if errors:
    print(f"⚠ 浏览器控制台错误 {len(errors)} 条：")
    for e in errors[:10]:
        print("  -", e[:200])
else:
    print("🎉 UI 走查全部通过，无浏览器控制台错误")
