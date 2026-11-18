"""
把下载好的 GGUF 直接注册进 Ollama 的本地模型库（不依赖 llama-quantize 校验）

Ollama 的模型库其实就是三层文件：
  models/blobs/sha256-xxx            内容块（模型权重 / 模板 / 参数）
  models/manifests/<registry>/<ns>/<model>/<tag>   清单（指向上面那些 blob）
所以可以手工拼一个，等价于 ollama create 的产物。
"""
import hashlib
import json
import shutil
from pathlib import Path

MODELS = Path(r"D:\foodai\models")
GGUF = MODELS / "gguf" / "qwen2.5-7b-instruct-q4_k_m.gguf"
BLOBS = MODELS / "blobs"
MANIFEST = MODELS / "manifests" / "registry.ollama.ai" / "library" / "qwen2.5" / "7b-instruct"

TEMPLATE = (
    "{{- if .System }}<|im_start|>system\n{{ .System }}<|im_end|>\n{{ end }}"
    "<|im_start|>user\n{{ .Prompt }}<|im_end|>\n<|im_start|>assistant\n"
)


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while True:
            b = f.read(16 * 1024 * 1024)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def put_blob(data: bytes) -> tuple[str, int]:
    d = hashlib.sha256(data).hexdigest()
    (BLOBS / f"sha256-{d}").write_bytes(data)
    return d, len(data)


BLOBS.mkdir(parents=True, exist_ok=True)
MANIFEST.parent.mkdir(parents=True, exist_ok=True)

print("计算模型文件哈希（4.7G，约 10 秒）…")
model_digest = sha256_file(GGUF)
model_size = GGUF.stat().st_size
target = BLOBS / f"sha256-{model_digest}"
if target.exists():
    GGUF.unlink()
    print("已存在同内容 blob，删除下载文件")
else:
    shutil.move(str(GGUF), str(target))
    print("模型已移入 blobs/（同一磁盘，瞬间完成）")

t_digest, t_size = put_blob(TEMPLATE.encode("utf-8"))
p_digest, p_size = put_blob(json.dumps(
    {"stop": ["<|im_start|>", "<|im_end|>"], "temperature": 0.7, "top_p": 0.8}
).encode("utf-8"))
lic_digest, lic_size = put_blob(b"Qwen2.5 is licensed under Apache-2.0.\n")

config = {
    "model_format": "gguf",
    "model_family": "qwen2",
    "model_families": ["qwen2"],
    "model_type": "7.4B",
    "file_type": "Q4_K_M",
    "architecture": "amd64",
    "os": "windows",
    "rootfs": {
        "type": "layers",
        "diff_ids": [f"sha256:{model_digest}", f"sha256:{t_digest}",
                     f"sha256:{p_digest}", f"sha256:{lic_digest}"],
    },
}
c_digest, c_size = put_blob(json.dumps(config, separators=(",", ":")).encode("utf-8"))

manifest = {
    "schemaVersion": 2,
    "mediaType": "application/vnd.docker.distribution.manifest.v2+json",
    "config": {
        "mediaType": "application/vnd.docker.container.image.v1+json",
        "digest": f"sha256:{c_digest}",
        "size": c_size,
    },
    "layers": [
        {"mediaType": "application/vnd.ollama.image.model",
         "digest": f"sha256:{model_digest}", "size": model_size},
        {"mediaType": "application/vnd.ollama.image.template",
         "digest": f"sha256:{t_digest}", "size": t_size},
        {"mediaType": "application/vnd.ollama.image.params",
         "digest": f"sha256:{p_digest}", "size": p_size},
        {"mediaType": "application/vnd.ollama.image.license",
         "digest": f"sha256:{lic_digest}", "size": lic_size},
    ],
}
MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
print("清单已写入:", MANIFEST)
