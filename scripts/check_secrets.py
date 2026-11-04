"""推送前的机密扫描：发现疑似 key / token / 私钥就退出码 1。

用法：
    python scripts/check_secrets.py              # 扫描所有已跟踪文件
    python scripts/check_secrets.py --staged     # 只扫暂存区（pre-commit 钩子用）

规则刻意宽松到「宁可误报」：命中后人工确认是不是真密钥。
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys

# (正则, 说明)
PATTERNS = [
    (re.compile(r"sk-[A-Za-z0-9]{16,}"), "OpenAI/智谱风格 key"),
    (re.compile(
        r"(?:CLOUD_API_KEY|ZHIPU_API_KEY|QWEN_API_KEY|JWT_SECRET|API_KEY|SECRET_KEY|ACCESS_TOKEN)"
        r"\s*[=:]\s*[^\s\"'{}]{12,}"
    ), "配置项疑似真实值"),
    (re.compile(r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----"), "私钥"),
    (re.compile(r"\b[0-9a-fA-F]{32,}\b"), "长十六进制串（可能是 key/secret）"),
]

# 模板/占位符，放过
ALLOW = re.compile(r"merged|get\(|\bxxx|your|example|示例|placeholder|<|>|\{|\}|\.\.\.")

# 文件名本身就不该进仓库（只针对配置/证书类扩展名，避免误伤 check_secrets.py 这种代码文件）
FORBIDDEN_NAMES = re.compile(
    r"(^|/)\.env($|\.)|\.pem$|\.key$|\.p12$|\.pfx$|(^|/)auth\.json$|(^|/)credentials[^/]*\.json$|(^|/)secrets[^/]*\.json$",
    re.IGNORECASE,
)

SKIP_SUFFIX = (".png", ".jpg", ".jpeg", ".gif", ".ico", ".woff", ".woff2", ".ttf", ".zip", ".exe", ".db", ".pyc")


def git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, encoding="utf-8", errors="ignore").stdout


def target_files(staged: bool) -> list[str]:
    if staged:
        out = git("diff", "--cached", "--name-only", "--diff-filter=ACM")
    else:
        out = git("-c", "core.quotepath=false", "ls-files")
    return [line for line in out.splitlines() if line.strip()]


def main() -> int:
    # Windows 控制台默认 GBK，统一切 UTF-8，避免符号/中文报编码错
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

    parser = argparse.ArgumentParser()
    parser.add_argument("--staged", action="store_true", help="只扫暂存区")
    args = parser.parse_args()

    problems: list[str] = []
    for path in target_files(args.staged):
        if FORBIDDEN_NAMES.search(path):
            problems.append(f"{path}: 文件名像机密文件，不应该提交")
            continue
        if path.lower().endswith(SKIP_SUFFIX):
            continue
        try:
            text = open(path, encoding="utf-8", errors="ignore").read()
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if ALLOW.search(line):
                continue
            for pattern, label in PATTERNS:
                if pattern.search(line):
                    problems.append(f"{path}:{lineno} 疑似{label}：{line.strip()[:80]}")
                    break

    if problems:
        print("发现疑似机密信息，已阻止提交：")
        for item in problems:
            print("  [X]", item)
        print("\n确认是误报可临时跳过：git commit --no-verify")
        return 1
    print(f"[OK] 已扫描 {len(target_files(args.staged))} 个文件，未发现机密信息")
    return 0


if __name__ == "__main__":
    sys.exit(main())
