"""
LLM 基准测速：同一批用例测当前配置下的"真实模型"耗时（绕过 mock 兜底）
用法：
  cd backend
  ./.venv/Scripts/python.exe bench_llm.py            # 按 .env 当前模式
  ./.venv/Scripts/python.exe bench_llm.py local      # 强制本地 Ollama
  ./.venv/Scripts/python.exe bench_llm.py cloud      # 强制云端智谱

用例：短聊天(非流式) / 流式聊天(首字+总时长) / 生成 1 天方案 / 食物识别 1 张图
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.stdout.reconfigure(encoding="utf-8")

parser = argparse.ArgumentParser(description="LLM 基准测速")
parser.add_argument("mode", nargs="?", choices=["local", "cloud"], help="强制指定模式（默认读 .env）")
args = parser.parse_args()
if args.mode:
    import os
    os.environ["LLM_MODE"] = args.mode   # 环境变量优先于 .env

from app.core.config import settings  # noqa: E402
from app.services.llm.provider import get_llm_provider  # noqa: E402

RUNS = 3

PROFILE = {
    "height_cm": 175, "weight_kg": 70, "bmi": 22.9, "age": 25, "gender": "male",
    "activity_level": "moderate", "goal": "lose", "daily_target_calories": 2000,
    "taste_preferences": ["清淡"], "dietary_restrictions": ["忌辣"],
    "exercise_preferences": ["跑步"],
}
CHAT = [{"role": "user", "content": "你好，请只用一句话回复：正常。"}]


def real_provider():
    """绕过 FallbackProvider，直接测真模型（否则 mock 秒回会掩盖真实耗时）"""
    p = get_llm_provider()
    return getattr(p, "real", p)


provider = real_provider()


def fmt(sec: float) -> str:
    return f"{sec:.2f}s"


def bench(name: str, fn, runs: int = RUNS):
    print(f"\n== {name} ==")
    times = []
    for i in range(1, runs + 1):
        t0 = time.perf_counter()
        try:
            extra = fn()
            dt = time.perf_counter() - t0
            times.append(dt)
            print(f"  第{i}次: {fmt(dt)}{'  ' + extra if extra else ''}")
        except Exception as e:  # noqa: BLE001
            print(f"  第{i}次: 失败 {type(e).__name__}: {str(e)[:120]}")
    if times:
        print(f"  平均: {fmt(sum(times) / len(times))}")


def run_chat():
    text = provider.chat(CHAT)
    return f"({len(text)} 字)"


def run_chat_stream():
    t0 = time.perf_counter()
    first, chunks = None, 0
    for piece in provider.chat_stream(CHAT):
        if first is None:
            first = time.perf_counter() - t0
        chunks += 1
    total = time.perf_counter() - t0
    return f"(首字 {fmt(first) if first is not None else '-'} / 总 {fmt(total)} / {chunks} 块)"


def run_day():
    day = provider.generate_day(PROFILE, 1, 7)
    return f"(第{day.day_index}天 {len(day.meals)} 餐)"


def run_vision():
    # 跳过测试脚本写入的假图（几十字节），取最大的一张真图；走生产同款压缩
    from app.services.images import compress_for_vision
    imgs = [p for p in Path(settings.UPLOAD_DIR).glob("*.*")
            if p.is_file() and p.stat().st_size > 2 * 1024]
    if not imgs:
        return "(uploads 无有效图片，跳过)"
    img = max(imgs, key=lambda p: p.stat().st_size)
    ctype = {"png": "image/png", "webp": "image/webp"}.get(img.suffix.lstrip(".").lower(), "image/jpeg")
    data, ctype = compress_for_vision(img.read_bytes(), ctype)
    v = provider.recognize_food(data, ctype)
    return f"({v.dish_name}, 压缩后 {len(data) / 1024:.0f}KB)"


print("=" * 60)
print(f"模式: {'local（本地 Ollama）' if settings.is_local else 'cloud（云端）'}   每用例 {RUNS} 次")
print(f"文字模型: {settings.text_model}  @ {settings.text_base_url}")
print(f"视觉模型: {settings.vision_model}  @ {settings.vision_base_url}")
print(f"超时: {settings.LLM_TIMEOUT}s   重试: {settings.LLM_MAX_RETRIES}")
print("=" * 60)

bench("短聊天（非流式）", run_chat)
bench("流式聊天（看首字延迟）", run_chat_stream)
bench("生成 1 天方案", run_day)
bench("食物识别（uploads 第一张图，跑 1 次）", run_vision, runs=1)
