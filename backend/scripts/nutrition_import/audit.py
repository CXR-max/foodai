"""活跃池质量体检 + 「可从 staging 替换」候选清单。

用法：
    python -m scripts.nutrition_import.audit
"""
import json
from pathlib import Path

from app.db.session import SessionLocal
from app.models.food import Food
from app.services.food_text import normalize_name

REPORT = Path(__file__).resolve().parents[2] / "import_reports" / "quality_report.json"


def _issues(food: Food) -> list[str]:
    out = []
    energy = food.energy_kcal
    protein = food.protein_g or 0
    fat = food.fat_g or 0
    carb = food.carb_g or 0
    if not energy or energy <= 0:
        out.append("缺热量")
    elif energy > 900:
        out.append("热量>900")
    elif abs(4 * protein + 9 * fat + 4 * carb - energy) / energy > 0.35:
        out.append("宏量不符")
    if min(protein, fat, carb) < 0 or max(protein, fat, carb) > 105:
        out.append("宏量异常")
    if not food.common_portion_g or food.common_portion_g <= 0:
        out.append("缺标准份")
    return out


def main() -> None:
    db = SessionLocal()
    try:
        active = db.query(Food).filter(Food.active.is_(True)).all()
        staging = db.query(Food).filter(Food.active.is_(False)).all()

        # 1) 质量问题
        broken = [(f.name_zh, _issues(f)) for f in active if _issues(f)]

        # 2) 重名
        seen = {}
        for f in active:
            seen.setdefault(f.name_norm or normalize_name(f.name_zh), []).append(f.name_zh)
        duplicates = {k: v for k, v in seen.items() if len(v) > 1}

        # 3) 替换候选：活跃里的估算条目，能否在 staging 找到同名/更可信的数据
        staging_by_norm = {}
        for f in staging:
            staging_by_norm.setdefault(f.name_norm or normalize_name(f.name_zh), []).append(f)
        replaceable = []
        for f in active:
            if f.source not in {"app_seed", "legacy_json"}:
                continue
            key = f.name_norm or normalize_name(f.name_zh)
            candidates = [s for s in staging_by_norm.get(key, []) if s.source == "tfda"]
            if not candidates:
                continue
            best = max(candidates, key=lambda s: (s.verified or False, s.id))
            if f.energy_kcal and best.energy_kcal:
                diff = (f.energy_kcal - best.energy_kcal) / best.energy_kcal
                replaceable.append({
                    "name": f.name_zh, "our_source": f.source, "our_kcal": f.energy_kcal,
                    "tfda_kcal": best.energy_kcal, "diff_pct": round(diff * 100, 1),
                })

        replaceable.sort(key=lambda x: abs(x["diff_pct"]), reverse=True)
        report = {
            "active": len(active),
            "staging": len(staging),
            "by_source": {},
            "issues": broken[:50],
            "issue_count": len(broken),
            "duplicates": duplicates,
            "replaceable_count": len(replaceable),
            "replaceable_top": replaceable[:30],
        }
        for f in active:
            report["by_source"][f.source] = report["by_source"].get(f.source, 0) + 1

        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

        print(f"活跃 {len(active)} / staging {len(staging)}")
        print("来源分布:", json.dumps(report["by_source"], ensure_ascii=False))
        print(f"质量问题 {len(broken)} 条:", [f"{n}:{','.join(i)}" for n, i in broken[:8]])
        print(f"重名 {len(duplicates)} 组")
        print(f"可在 TFDA 中找到同名数据的估算条目: {len(replaceable)} 条")
        for item in replaceable[:10]:
            print(f"  {item['name']}: 我们 {item['our_kcal']} vs TFDA {item['tfda_kcal']} ({item['diff_pct']:+}%)")
        print("报告:", REPORT)
    finally:
        db.close()


if __name__ == "__main__":
    main()
