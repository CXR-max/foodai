import argparse
import hashlib
import json
from pathlib import Path

from scripts.nutrition_import.canonical import import_rows, load_csv, load_excel


def main() -> None:
    parser = argparse.ArgumentParser(description="导入规范化营养数据（CSV/XLSX）")
    parser.add_argument("file", type=Path, help="CSV 或 XLSX 文件")
    parser.add_argument("--source", required=True, help="来源标识，如 usda_fndds、tfda、cofid")
    parser.add_argument("--version", default="1", help="数据版本")
    parser.add_argument("--license", default="unknown", help="许可标识")
    parser.add_argument("--source-type", default="official", help="official/recipe_estimate/manual")
    parser.add_argument("--commit", action="store_true", help="真正写入数据库；默认只做 dry-run")
    parser.add_argument("--force", action="store_true", help="覆盖同 source/source_id 的已有记录")
    parser.add_argument("--report", type=Path, help="把报告写到 JSON 文件")
    args = parser.parse_args()

    if args.file.suffix.lower() in {".xlsx", ".xlsm"}:
        rows = load_excel(args.file)
    else:
        rows = load_csv(args.file)
    checksum = hashlib.sha256(args.file.read_bytes()).hexdigest()
    result = import_rows(
        rows,
        source=args.source,
        version=args.version,
        license=args.license,
        source_type=args.source_type,
        dry_run=not args.commit,
        force=args.force,
        checksum=checksum,
    )
    text = json.dumps(result, ensure_ascii=False, indent=2)
    print(text)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
