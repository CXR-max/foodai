import argparse
import csv
from difflib import SequenceMatcher
from pathlib import Path

from app.services.llm import foods_data


def ingredient_overlap(left: list, right: list) -> float:
    left_set = {str(item).strip() for item in left if str(item).strip()}
    right_set = {str(item).strip() for item in right if str(item).strip()}
    if not left_set or not right_set:
        return 0.0
    return len(left_set & right_set) / len(left_set | right_set)


def energy_delta(left: dict, right: dict) -> float:
    a = left["per_100g"].get("calories") or 0
    b = right["per_100g"].get("calories") or 0
    base = max(abs(a), abs(b), 1)
    return abs(a - b) / base


def main() -> None:
    parser = argparse.ArgumentParser(description="生成同义/易混淆候选 CSV，供人工审核")
    parser.add_argument("--output", type=Path, default=Path("import_reports/confusable_candidates.csv"))
    parser.add_argument("--min-name", type=float, default=0.55)
    parser.add_argument("--min-ingredient", type=float, default=0.4)
    args = parser.parse_args()

    foods = [f for f in foods_data.load_foods() if f.get("active", True)]
    by_category: dict[str, list[dict]] = {}
    for food in foods:
        by_category.setdefault(food.get("category") or "其他", []).append(food)

    rows = []
    seen = set()
    for category, items in by_category.items():
        for i, left in enumerate(items):
            for right in items[i + 1:]:
                key = tuple(sorted((left["name"], right["name"])))
                if key in seen:
                    continue
                seen.add(key)
                name_score = SequenceMatcher(None, left["name_norm"], right["name_norm"]).ratio()
                overlap = ingredient_overlap(left.get("ingredients") or [], right.get("ingredients") or [])
                delta = energy_delta(left, right)
                if name_score < args.min_name and overlap < args.min_ingredient:
                    continue
                if name_score < 0.8 and delta < 0.15:
                    continue
                relation = "synonym_candidate" if delta < 0.12 and overlap >= 0.5 else "confusable_candidate"
                rows.append({
                    "relation": relation,
                    "group_hint": f"{category}:{left['name']}|{right['name']}",
                    "left_name": left["name"],
                    "right_name": right["name"],
                    "left_cuisine": left.get("cuisine", ""),
                    "right_cuisine": right.get("cuisine", ""),
                    "category": category,
                    "name_similarity": round(name_score, 3),
                    "ingredient_overlap": round(overlap, 3),
                    "energy_kcal_left": left["per_100g"].get("calories"),
                    "energy_kcal_right": right["per_100g"].get("calories"),
                    "energy_delta_ratio": round(delta, 3),
                    "left_ingredients": "|".join(str(x) for x in (left.get("ingredients") or [])),
                    "right_ingredients": "|".join(str(x) for x in (right.get("ingredients") or [])),
                    "left_tip": "",
                    "right_tip": "",
                })

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else [
            "relation", "group_hint", "left_name", "right_name", "left_cuisine",
            "right_cuisine", "category", "name_similarity", "ingredient_overlap",
            "energy_kcal_left", "energy_kcal_right", "energy_delta_ratio",
            "left_ingredients", "right_ingredients", "left_tip", "right_tip",
        ])
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {len(rows)} candidates to {args.output}")


if __name__ == "__main__":
    main()
