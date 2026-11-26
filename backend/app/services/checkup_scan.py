"""体检报告识别：图片 / PDF → 18 项指标（供用户核对后入库）

三条输入路径，各自走最近的路：
- 手机拍照：压缩后交给视觉模型（字小，长边必须比菜品识别大）
- 电子版 PDF（体检中心导出）：直接抽文字层交给文字模型，比视觉准、还省额度
- 扫描版 PDF（拍照拼的）：没有文字层，按 200dpi 渲染成图后走视觉

本模块只做「模型输出 → 目录指标」的搬运与纠错：
别名表对齐（各医院叫法不同）、单位换算（mg/dL 之类的旧制单位）、
离谱值拦截（识别错一位就必须让用户看见）。真正的分析在 services/checkups.py。

原则：这里永远不「猜」——对不上目录的项照实列进 unmatched，让用户自己补。
"""
from __future__ import annotations

import logging
import re
from datetime import datetime

from app.services.checkups import ITEM_MAP

logger = logging.getLogger("llm")

# ----------------------------------------------------------------------------
# 项目名别名：同一个指标各医院叫法不同，靠这张表兜住
# 匹配时长别名优先（否则「高密度脂蛋白胆固醇」会被「胆固醇」抢走）
# 英文别名 ≥3 字符才允许子串匹配，2 字符只允许完全相等（防 CR 命中 CRP）
# ----------------------------------------------------------------------------
ALIASES: dict[str, tuple[str, ...]] = {
    "height_cm": ("身高", "height"),
    "weight_kg": ("体重", "weight"),
    "bmi": ("bmi", "体质指数", "身体质量指数"),
    "body_fat_pct": ("体脂率", "体脂百分比", "体脂肪率", "bodyfat", "bf%"),
    "bmr_kcal": ("基础代谢率", "基础代谢", "bmr"),
    "waist_cm": ("腰围", "waist"),

    "glucose": ("空腹血糖", "血糖", "葡萄糖", "glu", "fpg", "fbg"),
    "hba1c": ("糖化血红蛋白", "糖化血红蛋白a1c", "hba1c", "ghb"),
    "uric_acid": ("血尿酸", "尿酸", "ua", "uric"),
    "tc": ("血清总胆固醇", "总胆固醇", "胆固醇", "tc", "chol", "t-cho"),
    "tg": ("甘油三酯", "三酰甘油", "tg"),
    "ldl": ("低密度脂蛋白胆固醇", "低密度脂蛋白", "ldl-c", "ldl"),
    "hdl": ("高密度脂蛋白胆固醇", "高密度脂蛋白", "hdl-c", "hdl"),

    "alt": ("丙氨酸氨基转移酶", "谷丙转氨酶", "alt", "gpt"),
    "ast": ("天门冬氨酸氨基转移酶", "谷草转氨酶", "ast", "got"),
    "creatinine": ("血肌酐", "肌酐", "creatinine", "crea", "scr"),
    "bun": ("尿素氮", "血尿素氮", "尿素", "bun", "urea"),

    "hemoglobin": ("血红蛋白", "hemoglobin", "hgb", "hb"),
    "tsh": ("促甲状腺激素", "tsh"),
}

# 命中这些词时禁用该 key（VLDL 里含 LDL，极低密度不是低密度）
EXCLUDES: dict[str, tuple[str, ...]] = {
    "ldl": ("vldl", "极低密度"),
    "hdl": ("vldl", "极低密度"),
    "tc": ("vldl", "极低密度", "高密度", "低密度"),
    "bun": ("尿素氮/肌酐",),
}

# 明细别名表，长的排前面
_FLAT_ALIASES: list[tuple[str, str]] = sorted(
    ((key, alias) for key, aliases in ALIASES.items() for alias in aliases),
    key=lambda pair: len(pair[1]),
    reverse=True,
)


def match_key(raw_name: str) -> str | None:
    """报告原文项目名 → 目录 key；对不上返回 None（不硬猜）"""
    norm = normalize_name(raw_name)
    if not norm:
        return None
    for key, alias in _FLAT_ALIASES:
        if any(word in norm for word in EXCLUDES.get(key, ())):
            continue
        if len(alias) <= 2 and alias.isascii():
            if norm == alias:            # 短英文缩写只认完全相等
                return key
            continue
        if alias in norm:
            return key
    return None


def normalize_name(raw: str) -> str:
    """统一项目名写法：去空格/括号/冒号，英文转小写。

    括号里通常是英文缩写（谷丙转氨酶(ALT)），去掉括号后仍能被子串命中，
    所以不必保留分隔符。
    """
    text = str(raw or "").lower()
    text = re.sub(r"[\s\u3000（）()\[\]【】〖〗:：,，、。·\-_/]", "", text)
    return text.strip()


# ----------------------------------------------------------------------------
# 单位换算：报告常用 mg/dL、g/dL 这类旧制单位，统一换到目录单位
# 只换「明确等价」的，不认识的原样保留并提示用户核对
# ----------------------------------------------------------------------------
def _unit_key(unit: str) -> str:
    return re.sub(r"[\s·]", "", str(unit or "")).lower()


_MGDL = {"mg/dl", "mg/100ml", "mg/100ml血"}

CONVERSIONS: dict[str, dict[str, tuple[float, str]]] = {
    # key: {原单位: (乘法因子, 说明)}
    "glucose": {u: (1 / 18.0, "mg/dL → mmol/L") for u in _MGDL},
    "tc": {u: (1 / 38.67, "mg/dL → mmol/L") for u in _MGDL},
    "ldl": {u: (1 / 38.67, "mg/dL → mmol/L") for u in _MGDL},
    "hdl": {u: (1 / 38.67, "mg/dL → mmol/L") for u in _MGDL},
    "tg": {u: (1 / 88.57, "mg/dL → mmol/L") for u in _MGDL},
    "uric_acid": {u: (59.48, "mg/dL → μmol/L") for u in _MGDL},
    "creatinine": {u: (88.4, "mg/dL → μmol/L") for u in _MGDL},
    "bun": {u: (1 / 2.8, "mg/dL → mmol/L") for u in _MGDL},
    "hemoglobin": {"g/dl": (10.0, "g/dL → g/L")},
    "height_cm": {"in": (2.54, "英寸 → cm"), "inch": (2.54, "英寸 → cm")},
    "waist_cm": {"in": (2.54, "英寸 → cm"), "inch": (2.54, "英寸 → cm")},
    "weight_kg": {"lb": (0.4536, "磅 → kg"), "lbs": (0.4536, "磅 → kg")},
}


def convert_unit(key: str, value: float, unit: str) -> tuple[float, str]:
    """返回 (换算后的值, 换算说明)；不需要换算时说明为空"""
    rules = CONVERSIONS.get(key)
    if not rules:
        return value, ""
    hit = rules.get(_unit_key(unit))
    if not hit:
        return value, ""
    factor, note = hit
    return value * factor, note


# 离谱值：远超出人体可能范围，基本只有识别错位这一种解释
SANITY: dict[str, tuple[float, float]] = {
    "height_cm": (100, 250), "weight_kg": (25, 250), "bmi": (10, 60),
    "body_fat_pct": (3, 60), "bmr_kcal": (800, 4000), "waist_cm": (40, 200),
    "glucose": (1, 40), "hba1c": (3, 20), "uric_acid": (50, 1200),
    "tc": (1, 20), "tg": (0.1, 30), "ldl": (0.2, 15), "hdl": (0.2, 5),
    "alt": (1, 5000), "ast": (1, 5000), "creatinine": (20, 1500),
    "bun": (0.5, 50), "hemoglobin": (30, 220), "tsh": (0.001, 100),
}


def _to_number(v) -> float | None:
    """从模型的 value 里取数字：兼容 5.2 / "5.2" / "5.2 ↑" / "<0.5" / "1,024" """
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        f = float(v)
        return f if f == f and abs(f) != float("inf") else None
    text = str(v).replace(",", "").replace("，", "")
    m = re.search(r"-?\d+(?:\.\d+)?", text)
    return float(m.group()) if m else None


# ----------------------------------------------------------------------------
# 模型输出 → 目录指标
# ----------------------------------------------------------------------------
def normalize_result(result, report_text: str = "") -> dict:
    """把模型原始输出整理成前端能直接填表的结构。

    items：对得上目录的项（含 key / 目录名 / 目录单位），前端按 key 回填
    unmatched：报告上有、但目录里没有的项，照实列出让用户自己决定
    warnings：需要用户核对的地方（离谱值、单位不认识、日期没认出）
    """
    items: list[dict] = []
    unmatched: list[dict] = []
    warnings: list[str] = []
    seen: set[str] = set()

    for raw in getattr(result, "items", []) or []:
        name = str(getattr(raw, "name", "") or "").strip()
        value = _to_number(getattr(raw, "value", None))
        unit = str(getattr(raw, "unit", "") or "").strip()
        if not name or value is None:
            continue

        key = match_key(name)
        if not key:
            unmatched.append({"name": name, "value": value, "unit": unit})
            continue
        if key in seen:                     # 同一项出现两次（如总胆固醇 + 胆固醇），先到的为准
            continue

        item = ITEM_MAP[key]
        converted_value, conv_note = convert_unit(key, value, unit)
        decimals = int(item.get("decimals", 1))
        converted_value = round(converted_value, max(decimals, 2))

        low, high = SANITY.get(key, (None, None))
        if low is not None and not (low <= converted_value <= high):
            warnings.append(
                f"{item['name']} 识别为 {converted_value:g} {item['unit']}，"
                f"超出常见范围，请对照原件核对"
            )

        seen.add(key)
        items.append({
            "key": key,
            "name": item["name"],
            "value": converted_value,
            "unit": item.get("unit") or "",
            "raw_name": name,
            "raw_value": value,
            "raw_unit": unit,
            "converted": bool(conv_note),
            "convert_note": conv_note,
        })

    if unmatched:
        names = "、".join(u["name"] for u in unmatched[:5])
        more = f" 等 {len(unmatched)} 项" if len(unmatched) > 5 else ""
        warnings.append(f"报告里这些项不在指标目录，未自动填入：{names}{more}")

    report_date = _parse_date(getattr(result, "report_date", ""))
    if not report_date and getattr(result, "report_date", ""):
        warnings.append("报告日期没认准，请手动确认")
    if not items:
        warnings.append("没有识别出可用的指标，请换一张更清楚的照片或改用手动填写")

    return {
        "is_report": bool(getattr(result, "is_report", True)),
        "report_date": report_date,
        "hospital": str(getattr(result, "hospital", "") or "").strip()[:100],
        "source_text_chars": len(report_text or ""),
        "items": items,
        "unmatched": unmatched,
        "warnings": warnings,
        "metric_count": len(items),
    }


def merge_payloads(payloads: list[dict]) -> dict:
    """多页 PDF 的识别结果合并：同项先到先得，提示语重新生成以免重复。"""
    if not payloads:
        return {"is_report": False, "report_date": "", "hospital": "", "source_text_chars": 0,
                "items": [], "unmatched": [], "warnings": ["没有可识别的内容"], "metric_count": 0}
    if len(payloads) == 1:
        return payloads[0]

    items: list[dict] = []
    unmatched: list[dict] = []
    warnings: list[str] = []
    seen: set[str] = set()
    for p in payloads:
        for it in p.get("items", []):
            if it["key"] in seen:
                continue
            seen.add(it["key"])
            items.append(it)
        for u in p.get("unmatched", []):
            if not any(x["name"] == u["name"] for x in unmatched):
                unmatched.append(u)
        # 逐页重复的结论性提示丢掉，最后统一重算
        warnings += [w for w in p.get("warnings", [])
                     if "不在指标目录" not in w and "没有识别出" not in w and "报告日期没认准" not in w]

    if unmatched:
        names = "、".join(u["name"] for u in unmatched[:5])
        more = f" 等 {len(unmatched)} 项" if len(unmatched) > 5 else ""
        warnings.append(f"报告里这些项不在指标目录，未自动填入：{names}{more}")
    if not items:
        warnings.append("没有识别出可用的指标，请换一张更清楚的照片或改用手动填写")

    return {
        "is_report": any(p.get("is_report", True) for p in payloads),
        "report_date": next((p["report_date"] for p in payloads if p.get("report_date")), ""),
        "hospital": next((p["hospital"] for p in payloads if p.get("hospital")), ""),
        "source_text_chars": sum(p.get("source_text_chars", 0) for p in payloads),
        "items": items,
        "unmatched": unmatched,
        "warnings": list(dict.fromkeys(warnings)),
        "metric_count": len(items),
    }


def _parse_date(raw) -> str:
    """把各种日期写法收敛成 YYYY-MM-DD；认不出就返回空串（不猜）"""
    text = str(raw or "").strip()
    if not text:
        return ""
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y年%m月%d日", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    m = re.search(r"(20\d{2})\D{1,3}(\d{1,2})\D{1,3}(\d{1,2})", text)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).strftime("%Y-%m-%d")
        except ValueError:
            return ""
    return ""


# ----------------------------------------------------------------------------
# PDF 分流：有文字层就抽文字，没有就渲染成图
# ----------------------------------------------------------------------------
PDF_MIN_TEXT_CHARS = 80     # 少于这个字数基本是扫描件（只有页眉页脚几行字）
PDF_MAX_PAGES = 2           # 体检报告通常 1~2 页，多了也没必要全认
PDF_RENDER_DPI = 200        # 化验单小字，150dpi 会糊


def pdf_payload(content: bytes) -> tuple[str, list[bytes]]:
    """解析 PDF：返回 (文字, 页面图片列表)。两者只会有一个非空。

    电子版报告抽出文字最准；扫描件没有文字层，渲染成图片交给视觉模型。
    """
    try:
        import pymupdf
    except ImportError as exc:      # noqa: BLE001 —— 依赖没装时给出明确指引
        raise ValueError("服务器缺少 PDF 解析组件，请改用图片上传") from exc

    try:
        doc = pymupdf.open(stream=content, filetype="pdf")
    except Exception as exc:        # noqa: BLE001
        raise ValueError("PDF 打不开，可能是文件损坏或加密") from exc

    try:
        if doc.needs_pass:
            raise ValueError("这份 PDF 有密码保护，请先解除密码再上传")

        pages = min(doc.page_count, PDF_MAX_PAGES)
        text = "\n".join(doc[i].get_text("text") for i in range(pages))
        text = re.sub(r"\n{2,}", "\n", text).strip()
        if len(text) >= PDF_MIN_TEXT_CHARS:
            logger.info("体检 PDF 走文字通道：%d 页，%d 字", pages, len(text))
            return text[:6000], []      # 截断防爆 token，体检报告前 6000 字足够

        images: list[bytes] = []
        for i in range(pages):
            pix = doc[i].get_pixmap(dpi=PDF_RENDER_DPI)
            images.append(pix.tobytes("png"))
        logger.info("体检 PDF 走视觉通道：%d 页渲染成图", len(images))
        return "", images
    finally:
        doc.close()


def image_payload(content: bytes) -> bytes:
    """图片先真解码校验，再按体检报告的长边压缩（比菜品识别大一档）"""
    from app.services.images import compress_for_vision, validate_image

    validate_image(content)                       # 坏图/伪造 MIME 直接抛 ValueError
    compressed, _ = compress_for_vision(content, "image/jpeg",
                                        max_edge=1600, quality=88)
    return compressed
