"""
大模型一键切换工具：改写 backend/.env，不需要手工编辑配置文件

用法（在 backend 目录下执行）：
    .venv\\Scripts\\python.exe switch_llm.py status              查看当前用的是哪个模型
    .venv\\Scripts\\python.exe switch_llm.py zhipu sk-xxxx       切到智谱（文字+视觉都走智谱）
    .venv\\Scripts\\python.exe switch_llm.py qwen  sk-xxxx       切到千问（阿里云百炼，文字+视觉）
    .venv\\Scripts\\python.exe switch_llm.py local [模型名]      文字走本地千问(11434)，视觉仍走智谱
    .venv\\Scripts\\python.exe switch_llm.py mock                强制模拟模式（零费用、离线可用）

说明：
- 只改 .env，不改代码。切完必须重启后端才生效。
- JWT_SECRET 等已有配置会原样保留。
- 不传 key 时保留 .env 里原来的 key；原来没有就提示你去申请。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ENV_PATH = Path(__file__).resolve().parent / ".env"

# ---- 各厂商的预设 ----
PROFILES = {
    "zhipu": {
        "name": "智谱 GLM（云端·免费档）",
        "LLM_MODE": "cloud",
        "CLOUD_BASE_URL": "https://open.bigmodel.cn/api/paas/v4",
        "CLOUD_TEXT_MODEL": "glm-4-flash",
        "CLOUD_VISION_MODEL": "glm-4.6v-flash",
        "LLM_TIMEOUT": "60",
        "LLM_DAY_MAX_TOKENS": "700",
        "apply_url": "https://open.bigmodel.cn  （注册后「API Keys」里创建，glm-4-flash 免费）",
    },
    "qwen": {
        "name": "千问 Qwen（阿里云百炼·云端）",
        "LLM_MODE": "cloud",
        "CLOUD_BASE_URL": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "CLOUD_TEXT_MODEL": "qwen-plus",
        "CLOUD_VISION_MODEL": "qwen-vl-plus",
        "LLM_TIMEOUT": "60",
        "LLM_DAY_MAX_TOKENS": "700",
        "apply_url": "https://bailian.console.aliyun.com  （开通百炼后创建 API-KEY，qwen-plus 有免费额度）",
    },
    "local": {
        "name": "本地千问 Qwen2.5-7B（文字本地，视觉仍走云端）",
        "LLM_MODE": "local",
        "LOCAL_TEXT_BASE_URL": "http://127.0.0.1:11434/v1",
        "LOCAL_TEXT_API_KEY": "ollama",
        "LOCAL_TEXT_MODEL": "qwen2.5:7b-instruct",
        "LLM_TIMEOUT": "300",  # CPU 跑本地模型慢（约 8 字/秒），放宽超时，避免没生成完就降级 mock
        # 7B 模型写 JSON 偏啰嗦，700 会在 exercise 字段前被截断，这里放到 1100 保证结构完整
        "LLM_DAY_MAX_TOKENS": "1100",
        "apply_url": "先双击 D:\\foodai\\启动本地模型.bat 把服务跑起来",
    },
    "mock": {
        "name": "模拟模式 Mock（零费用，不调用任何模型）",
        "LLM_MODE": "cloud",
        "LLM_TIMEOUT": "60",
        "LLM_DAY_MAX_TOKENS": "700",
        "apply_url": "无需 key",
    },
}

DEFAULT_KEY = ""

# 各厂商 key 在 .env 里的独立存放位置：切到另一家不会覆盖前一家
KEY_SLOTS = {
    "zhipu": "ZHIPU_API_KEY",
    "qwen": "QWEN_API_KEY",
}


def parse_env(text: str) -> dict[str, str]:
    data: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        data[k.strip()] = v.strip()
    return data


def detect(env: dict[str, str]) -> str:
    """根据 .env 内容判断当前是哪套配置"""
    mode = env.get("LLM_MODE", "cloud").lower()
    if mode == "local":
        return "local"
    key = env.get("CLOUD_API_KEY", "").strip()
    base = env.get("CLOUD_BASE_URL", "")
    if not key:
        return "mock"
    if "bigmodel.cn" in base:
        return "zhipu"
    if "dashscope" in base:
        return "qwen"
    return "custom"


def render(env: dict[str, str], profile: str, key: str | None) -> str:
    p = PROFILES[profile]
    merged = dict(env)
    for k, v in p.items():
        if k in ("name", "apply_url"):
            continue
        merged[k] = v

    # ---- key 处理 ----
    # 两家 key 各存各的槽位，切换模式时自动把对应那家的 key 写进 CLOUD_API_KEY
    slot = KEY_SLOTS.get(profile)
    if slot:
        if key:  # 命令行显式传入 → 更新这一家的槽位
            merged[slot] = key.strip()
        merged["CLOUD_API_KEY"] = merged.get(slot, "").strip()
    elif profile == "mock":
        merged["CLOUD_API_KEY"] = ""
    # local：CLOUD_API_KEY 保持原样，因为视觉仍走云端

    jwt = merged.get("JWT_SECRET") or "dev-secret-please-change"

    vision_note = {
        "zhipu": "视觉 = glm-4.6v-flash（免费）",
        "qwen": "视觉 = qwen-vl-plus",
        "local": "视觉仍走云端智谱（需要 CLOUD_API_KEY，否则识别会降级模拟）",
        "mock": "全部走本地模拟，不联网不花钱",
    }[profile]

    return f"""# ============================================================
# 识膳智行 后端配置（改完需要重启后端才生效）
# 本文件不入库（已在 .gitignore 中）
# 当前模式：{p["name"]}
# 切换方式：python switch_llm.py zhipu|qwen|local|mock   （或双击 切换模型.bat）
# ============================================================

# ---- 登录凭证 ----
JWT_SECRET={jwt}
JWT_EXPIRE_DAYS={merged.get("JWT_EXPIRE_DAYS", "7")}

# ---- 大模型开关 ----
# cloud = 文字 + 视觉都走云端 | local = 文字走本地千问服务，视觉走云端
# 当前：{vision_note}
LLM_MODE={merged["LLM_MODE"]}

# ---- 云端配置 ----
# {p["name"]}：{p["apply_url"]}
CLOUD_BASE_URL={merged["CLOUD_BASE_URL"]}
CLOUD_API_KEY={merged["CLOUD_API_KEY"]}
CLOUD_TEXT_MODEL={merged["CLOUD_TEXT_MODEL"]}
CLOUD_VISION_MODEL={merged["CLOUD_VISION_MODEL"]}

# ---- 两家各自的 Key（切来切去不用重新粘贴；上面 CLOUD_API_KEY 由这里自动带出） ----
ZHIPU_API_KEY={merged.get("ZHIPU_API_KEY", "")}
QWEN_API_KEY={merged.get("QWEN_API_KEY", "")}

# ---- 本地千问服务（llama.cpp，仅 LLM_MODE=local 时用于文字） ----
LOCAL_TEXT_BASE_URL={merged.get("LOCAL_TEXT_BASE_URL", "http://127.0.0.1:11434/v1")}
LOCAL_TEXT_API_KEY={merged.get("LOCAL_TEXT_API_KEY", "ollama")}
LOCAL_TEXT_MODEL={merged.get("LOCAL_TEXT_MODEL", "qwen2.5:7b-instruct")}

# ---- 调用治理 ----
LLM_TIMEOUT={merged["LLM_TIMEOUT"]}
LLM_MAX_RETRIES={merged.get("LLM_MAX_RETRIES", "0")}
LLM_MAX_TOKENS={merged.get("LLM_MAX_TOKENS", "2048")}
LLM_DAY_MAX_TOKENS={merged.get("LLM_DAY_MAX_TOKENS", "700")}
LLM_CHAT_MAX_TOKENS={merged.get("LLM_CHAT_MAX_TOKENS", "128")}
LLM_VISION_MAX_TOKENS={merged.get("LLM_VISION_MAX_TOKENS", "300")}
LLM_FALLBACK_TO_MOCK={merged.get("LLM_FALLBACK_TO_MOCK", "True")}
"""


def main() -> int:
    args = sys.argv[1:]
    if not args or args[0] in ("status", "-h", "--help", "help"):
        env = parse_env(ENV_PATH.read_text(encoding="utf-8")) if ENV_PATH.exists() else {}
        cur = detect(env)
        print("当前配置：", PROFILES.get(cur, {}).get("name", cur))
        print("  配置文件：", ENV_PATH)
        print("  LLM_MODE =", env.get("LLM_MODE", "?"))
        print("  文字模型 =", env.get("LOCAL_TEXT_MODEL") if env.get("LLM_MODE") == "local" else env.get("CLOUD_TEXT_MODEL"))
        print("  视觉模型 =", env.get("CLOUD_VISION_MODEL"))
        print("  CLOUD_API_KEY =", ("已填写（%s…）" % env.get("CLOUD_API_KEY", "")[:8]) if env.get("CLOUD_API_KEY") else "未填写")
        for name, slot in KEY_SLOTS.items():
            v = env.get(slot, "")
            label = PROFILES[name]["name"].split("（")[0]
            print(f"  {label} key =", ("已保存（%s…）" % v[:8]) if v else "未保存")
        if args and args[0] in ("-h", "--help", "help"):
            print("\n用法：python switch_llm.py zhipu|qwen|local|mock [key或模型名]")
        return 0

    profile = args[0].lower()
    if profile not in PROFILES:
        print(f"未知模式：{profile}\n可选：zhipu / qwen / local / mock / status")
        return 2

    env = parse_env(ENV_PATH.read_text(encoding="utf-8")) if ENV_PATH.exists() else {}

    key: str | None = None
    if profile in ("zhipu", "qwen"):
        key = args[1] if len(args) > 1 else None
    elif profile == "local":
        model = args[1] if len(args) > 1 else None
        if model:
            env["LOCAL_TEXT_MODEL"] = model

    text = render(env, profile, key)
    ENV_PATH.write_text(text, encoding="utf-8")

    cur_key = parse_env(text).get("CLOUD_API_KEY", "")
    print(f"[OK] 已切换到：{PROFILES[profile]['name']}")
    if profile in ("zhipu", "qwen"):
        if cur_key:
            print(f"  使用 {KEY_SLOTS[profile]} = {cur_key[:8]}…")
        else:
            print(f"[注意] 还没保存 {KEY_SLOTS[profile]} — 现在仍是模拟模式。去这里申请：{PROFILES[profile]['apply_url']}")
            print(f"  拿到后执行：python switch_llm.py {profile} sk-你的key")
    print("→ 请重启后端（关掉后端窗口，重新双击 启动.bat）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
