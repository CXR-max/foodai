"""手环数据文件解析：把「导出的文件」变成统一的一天一条指标

支持的输入（按实用性排序，都是手机端能真正拿到的东西）：
1. **ZIP 数据包**：小米运动健康 / Zepp Life 的「数据导出」产物，解包后自动合并里面的 CSV/JSON
2. **CSV**：自己整理的表、Gadgetbridge「活动数据」导出、Excel 另存为 CSV
3. **JSON**：小米运动健康/Zepp Life 的账号数据包、第三方脚本的输出、本项目的导出格式
4. **Apple Health XML**：iPhone 上「健康」App 导出的 export.xml（含步数/心率/睡眠）

解析完先用原始 dict 返回，交给路由层做校验与入库，
这样"文件能读出来"和"数据能不能用"两件事分开报错，用户看得懂。
"""
from __future__ import annotations

import csv
import io
import json
import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import date, datetime

# ---- 字段别名（中英文都收，手环导出的表头五花八门）----
FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "metric_date": ("date", "day", "日期", "时间", "日期时间", "day_date", "metric_date"),
    "steps": ("steps", "step", "步数", "总步数", "计步", "step_count"),
    "distance_km": ("distance", "distance_km", "距离", "distance(km)", "公里"),
    "calories": ("calories", "calorie", "kcal", "消耗", "卡路里", "热量", "热量消耗"),
    "active_minutes": ("active_minutes", "运动时长", "活动分钟", "active_min"),
    "floors": ("floors", "楼层", "爬楼", "floors_climbed"),
    "hr_avg": ("hr_avg", "avg_heart_rate", "平均心率", "心率"),
    "hr_min": ("hr_min", "min_heart_rate", "最小心率"),
    "hr_max": ("hr_max", "max_heart_rate", "最大心率"),
    "hr_resting": ("hr_resting", "resting_heart_rate", "静息心率"),
    "sleep_minutes": ("sleep_minutes", "睡眠时长", "睡眠", "sleep", "sleep_min"),
    "deep_sleep_minutes": ("deep_sleep_minutes", "深睡", "深睡时长", "deep"),
    "light_sleep_minutes": ("light_sleep_minutes", "浅睡", "浅睡时长", "light"),
    "rem_sleep_minutes": ("rem_sleep_minutes", "rem", "快速眼动"),
    "awake_minutes": ("awake_minutes", "清醒时长", "夜间清醒", "awake"),
    "spo2": ("spo2", "血氧", "oxygen_saturation"),
    "stress": ("stress", "压力", "压力值"),
}

_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")


def _to_float(v):
    """从 "8,432 步" / "82 %" / 82.0 里抠出数字"""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = _NUM_RE.search(str(v).replace(",", ""))
    return float(m.group()) if m else None


def _pick(row: dict, key: str):
    """按别名表取值，不区分大小写和空格；「距离(km)」这类带单位后缀也能匹配"""
    variants: dict[str, object] = {}
    for k, v in row.items():
        name = str(k).strip().lower()
        if not name:
            continue
        variants[name] = v
        # 去掉括号单位后缀：距离(km) → 距离、distance(km) → distance
        bare = re.sub(r"[（(].*?[)）]", "", name).strip()
        if bare and bare != name:
            variants.setdefault(bare, v)
    for alias in FIELD_ALIASES[key]:
        if alias.lower() in variants:
            return variants[alias.lower()]
    return None


def _norm_row(row: dict) -> dict:
    """一行原始数据 → 标准字段（只保留有值的）"""
    out: dict = {}
    raw_date = _pick(row, "metric_date")
    if raw_date:
        out["metric_date"] = _norm_date(raw_date)

    for key in ("steps", "active_minutes", "floors", "hr_avg", "hr_min", "hr_max",
                "hr_resting", "deep_sleep_minutes",
                "light_sleep_minutes", "rem_sleep_minutes", "awake_minutes", "stress"):
        val = _to_float(_pick(row, key))
        if val is not None:
            out[key] = int(round(val))

    for key in ("distance_km", "calories", "spo2"):
        val = _to_float(_pick(row, key))
        if val is not None:
            out[key] = round(val, 2)

    # 睡眠单独处理：有的表给的是小时（7.5），有的是分钟（450）
    sleep_val = _to_float(_pick(row, "sleep_minutes"))
    if sleep_val is not None:
        if sleep_val <= 24 and not float(sleep_val).is_integer():
            sleep_val *= 60          # 7.5 → 450 分钟
        out["sleep_minutes"] = int(round(sleep_val))
    return out


def _norm_date(v) -> str | None:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    s = str(v).strip()
    m = re.match(r"(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})", s)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    if re.fullmatch(r"\d{8}", s):
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return s[:10] if len(s) >= 10 else None


# ---------------------------------------------------------------- CSV
def _parse_csv(text: str) -> list[dict]:
    # 有些导出带 BOM 或分号分隔
    text = text.lstrip("\ufeff")
    sample = text[:2048]
    delim = ";" if sample.count(";") > sample.count(",") else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delim)
    rows = []
    for row in reader:
        metric = _norm_row({k: v for k, v in row.items() if k})
        if metric.get("metric_date"):
            rows.append(metric)
    return rows


# ---------------------------------------------------------------- JSON
def _json_rows(obj) -> list[dict]:
    """把各种 JSON 结构摊平成「一行一天」"""
    rows: list[dict] = []

    if isinstance(obj, list):
        for item in obj:
            if isinstance(item, dict):
                nested = item.get("metrics") or item.get("items")
                if isinstance(nested, list) and not _pick(item, "steps"):
                    rows.extend(_json_rows(nested))
                else:
                    rows.append(item)
        return rows

    if not isinstance(obj, dict):
        return rows

    # 本项目自己的导出格式
    if isinstance(obj.get("metrics"), list):
        return _json_rows(obj["metrics"])
    if isinstance(obj.get("items"), list):
        return _json_rows(obj["items"])

    # Zepp Life / 小米运动健康 账号包：data.steps = [{date|time, value}, ...]
    data = obj.get("data") if isinstance(obj.get("data"), dict) else None
    if data:
        for key, target in (
            ("steps", "steps"), ("distance", "distance_km"), ("calories", "calories"),
            ("heart_rate", "hr_avg"), ("sleep", "sleep_minutes"), ("spo2", "spo2"),
        ):
            series = data.get(key)
            if isinstance(series, list):
                for point in series:
                    if isinstance(point, dict):
                        rows.append({
                            "metric_date": point.get("date") or point.get("time")
                            or point.get("day") or point.get("startDate"),
                            target: point.get("value") if target != "hr_avg"
                            else (point.get("avg") or point.get("value")),
                        })
        if rows:
            return rows

    # 单条对象
    if _pick(obj, "steps") is not None or _pick(obj, "metric_date") is not None:
        rows.append(obj)
    return rows


def _parse_json(text: str) -> list[dict]:
    obj = json.loads(text)
    merged: dict[str, dict] = {}
    for row in _json_rows(obj):
        metric = _norm_row(row)
        day = metric.pop("metric_date", None)
        if not day:
            continue
        bucket = merged.setdefault(day, {"metric_date": day})
        # 同一指标多次出现时，步数/消耗累加，心率/睡眠取平均或最大值
        for k, v in metric.items():
            if k not in bucket:
                bucket[k] = v
            elif k in ("steps", "calories", "distance_km", "active_minutes", "floors"):
                bucket[k] = (bucket[k] or 0) + v
            elif k in ("hr_max",):
                bucket[k] = max(bucket[k], v)
            elif k in ("hr_min",):
                bucket[k] = min(bucket[k], v)
            elif k in ("hr_avg", "spo2"):
                bucket[k] = round((bucket[k] + v) / 2, 1) if bucket[k] else v
            else:
                bucket[k] = v
    return list(merged.values())


# ---------------------------------------------------------------- Apple Health XML
_HK_MAP = {
    "HKQuantityTypeIdentifierStepCount": ("steps", "sum"),
    "HKQuantityTypeIdentifierDistanceWalkingRunning": ("distance_km", "sum"),
    "HKQuantityTypeIdentifierActiveEnergyBurned": ("calories", "sum"),
    "HKQuantityTypeIdentifierHeartRate": ("hr_avg", "avg"),
    "HKQuantityTypeIdentifierRestingHeartRate": ("hr_resting", "avg"),
    "HKQuantityTypeIdentifierOxygenSaturation": ("spo2", "avg"),
}


def _parse_apple_xml(content: bytes) -> list[dict]:
    """流式读取 export.xml，按天聚合（文件可能几十 MB，不能一次性建树）"""
    sums: dict[str, dict] = defaultdict(lambda: defaultdict(float))
    samples: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))

    context = ET.iterparse(io.BytesIO(content), events=("end",))
    for _, elem in context:
        if elem.tag == "Record":
            rtype = elem.get("type")
            start, end = elem.get("startDate"), elem.get("endDate")
            value = _to_float(elem.get("value"))
            day = _norm_date(start)

            if rtype in _HK_MAP and day and value is not None:
                key, how = _HK_MAP[rtype]
                if how == "sum":
                    amount = value
                    if key == "distance_km":
                        unit = (elem.get("unit") or "").lower()
                        if unit in ("m", "meter", "meters"):
                            amount = value / 1000
                        elif "mi" in unit:
                            amount = value * 1.60934
                    elif key == "calories" and (elem.get("unit") or "").lower() == "kj":
                        amount = value / 4.184
                    sums[day][key] += amount
                else:
                    if key == "spo2" and value <= 1:   # Apple 用 0~1 表示血氧
                        value *= 100
                    samples[day][key].append(value)

            elif rtype == "HKCategoryTypeIdentifierSleepAnalysis" and day and start and end:
                v = (elem.get("value") or "").lower()
                if "awake" not in v and "inbed" not in v:
                    try:
                        s = datetime.strptime(start[:19], "%Y-%m-%d %H:%M:%S")
                        e = datetime.strptime(end[:19], "%Y-%m-%d %H:%M:%S")
                        sums[day]["sleep_minutes"] += max(0.0, (e - s).total_seconds() / 60)
                    except ValueError:
                        pass
            elem.clear()

    rows: list[dict] = []
    for day in set(sums) | set(samples):
        row: dict = {"metric_date": day}
        s, sp = sums.get(day, {}), samples.get(day, {})
        if s.get("steps"):
            row["steps"] = int(round(s["steps"]))
        if s.get("distance_km"):
            row["distance_km"] = round(s["distance_km"], 2)
        if s.get("calories"):
            row["calories"] = round(s["calories"], 1)
        if s.get("sleep_minutes", 0) >= 1:
            row["sleep_minutes"] = int(round(s["sleep_minutes"]))
        for key in ("hr_avg", "hr_resting"):
            if sp.get(key):
                row[key] = int(round(sum(sp[key]) / len(sp[key])))
        if sp.get("hr_avg"):
            row["hr_min"] = int(min(sp["hr_avg"]))
            row["hr_max"] = int(max(sp["hr_avg"]))
        if sp.get("spo2"):
            row["spo2"] = round(sum(sp["spo2"]) / len(sp["spo2"]), 1)
        if len(row) > 1:
            rows.append(row)
    return rows


# ---------------------------------------------------------------- 入口
def _parse_zip(content: bytes) -> list[dict]:
    """小米运动健康 / Zepp Life 导出的数据包（zip）：解压后把里面的 CSV/JSON 按天合并

    为什么不直接看扩展名：这些 App 的导出包内部命名五花八门（英文、拼音、
    带日期后缀都有），只能靠别名表逐个试；读不出来的成员跳过，不会因为
    包里混了张图片或说明文件就整包失败。
    合并规则：同一天的第一步先用先读到的非空值，后面文件只补空缺，不覆盖也不累加
    （步数累加会把「运动记录」和「每日汇总」重复计算，反而失真）。
    """
    import zipfile

    try:
        zf = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as e:
        raise ValueError("压缩包已损坏，请重新导出后再上传") from e

    names = [
        n for n in zf.namelist()
        if not n.endswith("/")
        and "__MACOSX" not in n
        and n.rsplit("/", 1)[-1][:1] != "."          # 跳过隐藏文件 / 资源叉
        and n.lower().endswith((".csv", ".tsv", ".json", ".txt", ".xml"))
    ]
    if not names:
        raise ValueError(
            "压缩包里没有可读的数据文件（只支持 CSV / JSON / XML；"
            "如果是 Excel 的 .xlsx，请先在里面「另存为 CSV」再上传）"
        )

    merged: dict[str, dict] = {}
    for name in names:
        try:
            raw = zf.read(name)
        except (KeyError, RuntimeError):
            continue
        if len(raw) > 80 * 1024 * 1024:
            continue
        try:
            rows = parse_metric_file(name.rsplit("/", 1)[-1], raw)
        except ValueError:
            continue                                  # 单个成员格式不对就跳过
        for row in rows:
            day = row.get("metric_date")
            if not day:
                continue
            slot = merged.setdefault(day, {"metric_date": day})
            for key, value in row.items():
                if key != "metric_date" and value is not None:
                    slot.setdefault(key, value)

    if not merged:
        raise ValueError("压缩包里的文件都读不出手环数据，请确认导出的数据结构")
    return [merged[d] for d in sorted(merged)]


def parse_metric_file(filename: str, content: bytes) -> list[dict]:
    """按扩展名/内容猜测格式并解析，返回 [{metric_date, steps, ...}, ...]"""
    name = (filename or "").lower()
    head = content[:400].lstrip()

    # 导出包（小米运动健康 / Zepp Life）是 zip，先解包再逐份解析
    if content[:4] == b"PK\x03\x04" or name.endswith(".zip"):
        return _parse_zip(content)

    if name.endswith(".xml") or head.startswith(b"<?xml") or b"<HealthData" in content[:5000]:
        return _parse_apple_xml(content)

    text = None
    for enc in ("utf-8-sig", "utf-8", "gbk", "latin-1"):
        try:
            text = content.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise ValueError("文件编码无法识别，请另存为 UTF-8 或 CSV 后重试")

    stripped = text.lstrip()
    if name.endswith(".json") or stripped[:1] in ("[", "{"):
        try:
            return _parse_json(text)
        except json.JSONDecodeError as e:
            raise ValueError(f"JSON 格式有误：第 {e.lineno} 行 {e.msg}") from e

    if name.endswith((".csv", ".tsv", ".txt")) or "," in stripped[:200]:
        return _parse_csv(text)

    raise ValueError("无法识别的文件格式，请上传 CSV / JSON / ZIP 数据包 / Apple Health 的 export.xml")


def summarize_rows(rows: list[dict]) -> dict:
    """给前端一个「解析结果预览」，让用户确认后再入库"""
    if not rows:
        return {"days": 0, "start": None, "end": None, "fields": [], "preview": []}
    days = sorted(r["metric_date"] for r in rows if r.get("metric_date"))
    fields = sorted({k for r in rows for k in r if k != "metric_date"})
    return {
        "days": len(days),
        "start": days[0] if days else None,
        "end": days[-1] if days else None,
        "fields": fields,
        "preview": rows[:5],
    }


def rows_to_metrics(rows: list[dict]) -> list[dict]:
    """只保留能入库的字段，并裁掉异常值（防止导入把图表拉爆）"""
    limit = {
        "steps": (0, 300000), "active_minutes": (0, 1440), "floors": (0, 5000),
        "hr_avg": (20, 250), "hr_min": (20, 250), "hr_max": (20, 250), "hr_resting": (20, 250),
        "sleep_minutes": (0, 1440), "deep_sleep_minutes": (0, 1440),
        "light_sleep_minutes": (0, 1440), "rem_sleep_minutes": (0, 1440),
        "awake_minutes": (0, 1440), "distance_km": (0, 500),
        "calories": (0, 20000), "spo2": (50, 100), "stress": (0, 100),
    }
    out = []
    for row in rows:
        if not row.get("metric_date"):
            continue
        clean = {"metric_date": row["metric_date"]}
        for k, (lo, hi) in limit.items():
            v = row.get(k)
            if v is None:
                continue
            try:
                v = float(v)
            except (TypeError, ValueError):
                continue
            if lo <= v <= hi:
                clean[k] = v
        if len(clean) > 1:
            out.append(clean)
    return out
