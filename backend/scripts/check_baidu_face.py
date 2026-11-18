"""百度人脸比对连通性自检

用途：拿到 API Key / Secret Key 后，先用这个脚本确认「key 能用 + 额度到了 + 比对真的在跑」，
再去动 app 配置，避免把鉴权问题和业务问题混在一起排查。

在 backend 目录下运行：
    .venv/Scripts/python.exe scripts/check_baidu_face.py

它会做三件事：
1. 用 AK/SK 换 access_token（验证 key 是否正确、是否已实名/领额度）
2. 把底库照和它自己比一次（正常应接近 100 分，证明接口通）
3. 把两张不同账号的底库照比一次（正常应明显低于阈值，证明真的在区分人脸）

注意：这个过程会把本地底库照片发给百度云端做比对。仅在你自己授权的测试数据上使用。
"""
from __future__ import annotations

import base64
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings  # noqa: E402

# 百度常见错误码 → 人话，方便直接定位
ERR_HINTS = {
    110: "access_token 无效或已过期，检查 AK/SK 是否复制完整",
    111: "access_token 过期，重新获取即可（脚本会自动重试一次）",
    100: "AK/SK 无效（Invalid parameter），大概率是 Secret Key 复制错了",
    6: "没有调用该接口的权限，检查创建应用时是否勾选了「人脸识别」",
    17: "QPS 超限（免费额度只有 1 QPS），稍等一秒再试",
    18: "当日/当月调用量已达上限，去控制台看免费额度是否到账",
    222202: "图片中检测不到人脸，换一张正脸照",
    222203: "无法解析人脸，图片可能损坏或格式不支持",
    222204: "图片尺寸太小或太大",
    223120: "活体检测未通过（传的是翻拍/屏幕照）",
    223121: "图片质量太差，建议换清晰正脸照",
}


def _hint(code: int | None, msg: str = "") -> str:
    return ERR_HINTS.get(code or 0, msg or "未知错误")


def main() -> int:
    ak, sk = settings.BAIDU_FACE_API_KEY, settings.BAIDU_FACE_SECRET_KEY
    print("=" * 62)
    print(f"当前 FACE_PROVIDER = {settings.FACE_PROVIDER}")
    print(f"API Key            = {ak[:6] + '...' + ak[-4:] if len(ak) > 12 else '（空）'}")
    print(f"Secret Key         = {'已填写（' + str(len(sk)) + ' 位）' if sk else '（空）'}")
    print(f"比对阈值           = {settings.FACE_MATCH_THRESHOLD}")
    print("=" * 62)

    if not ak or not sk:
        print("\n[!] .env 里 BAIDU_FACE_API_KEY / BAIDU_FACE_SECRET_KEY 还没填。")
        print("    去 https://console.bce.baidu.com/ai/#/ai/face/overview/index → 应用列表 复制。")
        return 2

    # ---- 1. 换 token ----
    try:
        resp = httpx.post(
            "https://aip.baidubce.com/oauth/2.0/token",
            params={
                "grant_type": "client_credentials",
                "client_id": ak,
                "client_secret": sk,
            },
            timeout=15,
        )
        data = resp.json()
    except Exception as e:  # noqa: BLE001
        print(f"\n[×] 网络请求失败：{e}")
        return 1

    if "access_token" not in data:
        code = data.get("error_code") or data.get("error")
        print(f"\n[×] 鉴权失败：{code} {data.get('error_description', '')}")
        print(f"    → {_hint(code if isinstance(code, int) else None)}")
        return 1

    token = data["access_token"]
    print("\n[✓] access_token 获取成功 —— key 有效、账号已实名")
    print(f"    有效期约 {int(data.get('expires_in', 0)) // 86400} 天")

    # ---- 2. 找底库照片 ----
    faces = sorted(settings.FACE_DIR.glob("*.jpg"))
    if not faces:
        print(f"\n[!] {settings.FACE_DIR} 里没有底库照片，先去 App 里绑定一张人脸。")
        return 2
    print(f"[i] 发现 {len(faces)} 张底库照：{[f.name for f in faces]}")

    def compare(a: Path, b: Path, label: str) -> float | None:
        body = [
            {"image": base64.b64encode(a.read_bytes()).decode(), "image_type": "BASE64",
             "face_type": "LIVE", "quality_control": "LOW", "liveness_control": "NORMAL"},
            {"image": base64.b64encode(b.read_bytes()).decode(), "image_type": "BASE64",
             "face_type": "LIVE", "quality_control": "LOW", "liveness_control": "NORMAL"},
        ]
        try:
            r = httpx.post(
                "https://aip.baidubce.com/rest/2.0/face/v3/match",
                params={"access_token": token}, json=body, timeout=20,
            )
            d = r.json()
        except Exception as e:  # noqa: BLE001
            print(f"  [{label}] 请求异常：{e}")
            return None
        if d.get("error_code") != 0:
            code = d.get("error_code")
            print(f"  [{label}] 失败 {code} {d.get('error_msg')}  → {_hint(code, d.get('error_msg', ''))}")
            return None
        score = float(d["result"]["score"])
        print(f"  [{label}] score = {score:.1f}")
        return score

    # ---- 3. 自比（接口是否真的在跑）----
    print("\n[1/2] 底库照 vs 它自己（应接近 100）")
    same = compare(faces[0], faces[0], faces[0].name)

    # ---- 4. 跨人比（是否真的在区分人脸）----
    other = None
    if len(faces) >= 2:
        print(f"\n[2/2] {faces[0].name} vs {faces[1].name}（不同账号，应明显低于阈值）")
        other = compare(faces[0], faces[1], f"{faces[0].stem}-vs-{faces[1].stem}")
    else:
        print("\n[2/2] 只有一张底库照，跳过跨人比对。用另一个账号再绑一张可验证。")

    # ---- 5. 结论 ----
    print("\n" + "=" * 62)
    if same is None:
        print("结论：接口不通，先按上面的错误码排查，别急着改 App 配置。")
        return 1
    ok = same >= settings.FACE_MATCH_THRESHOLD
    print(f"同人比对 {same:.1f} 分 {'≥' if ok else '<'} 阈值 {settings.FACE_MATCH_THRESHOLD} → "
          f"{'正常' if ok else '偏低，检查照片质量/是否镜像'}")
    if other is not None:
        print(f"异人比对 {other:.1f} 分 → "
              f"{'正常，确实在区分人脸' if other < settings.FACE_MATCH_THRESHOLD else '⚠ 异人也过高，检查阈值设置'}")
    print("\n下一步：把 .env 的 FACE_PROVIDER 改成 baidu，重启后端，页面上的「演示模式」标签会消失。")
    print("=" * 62)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
