# 把本地 HTML 渲染成 PNG（截图/汇报用）
# 用法：
#   backend/.venv/Scripts/python.exe scripts/html_shot.py docs/mobile-5-modules.html
#   backend/.venv/Scripts/python.exe scripts/html_shot.py in.html out.png --width 1180
# 依赖：playwright（调用本机 Edge，不下载浏览器）
import argparse
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    ap = argparse.ArgumentParser(description="HTML → PNG")
    ap.add_argument("html", help="输入 HTML 路径")
    ap.add_argument("out", nargs="?", default=None, help="输出 PNG 路径，默认同名 .png")
    ap.add_argument("--width", type=int, default=1180, help="视口宽度（默认 1180）")
    ap.add_argument("--scale", type=float, default=2.0, help="像素倍率（默认 2，二倍图更清晰）")
    args = ap.parse_args()

    src = Path(args.html)
    if not src.is_absolute():
        src = ROOT / src
    if not src.exists():
        print(f"找不到文件：{src}")
        return 1

    dst = Path(args.out) if args.out else src.with_suffix(".png")
    if not dst.is_absolute():
        dst = ROOT / dst
    dst.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(
            viewport={"width": args.width, "height": 900},
            device_scale_factor=args.scale,
        )
        page.goto(src.as_uri(), wait_until="networkidle")
        page.wait_for_timeout(400)
        page.screenshot(path=str(dst), full_page=True)
        browser.close()

    print(f"已生成：{dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
