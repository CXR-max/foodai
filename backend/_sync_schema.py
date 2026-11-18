"""补齐数据库：按 SQLAlchemy 模型建缺失表 + 给已有表补缺失列。

只增不删，不改已有数据。
"""
import sys

sys.dont_write_bytecode = True

from sqlalchemy import inspect, text

from app.db.session import Base, engine
import app.models  # noqa: F401  触发全部模型注册

insp = inspect(engine)
created = []
added_cols = []


def default_for(col):
    try:
        if col.default is not None and getattr(col.default, "arg", None) is not None and not callable(col.default.arg):
            v = col.default.arg
            if isinstance(v, bool):
                return "0" if not v else "1"
            if isinstance(v, (int, float)):
                return str(v)
            if isinstance(v, str):
                return "'" + v.replace("'", "''") + "'"
    except Exception:
        pass
    return None


with engine.begin() as conn:
    # 1) 建缺失表
    existing = set(insp.get_table_names())
    missing_tables = [t for name, t in Base.metadata.tables.items() if name not in existing]
    if missing_tables:
        Base.metadata.create_all(bind=conn, tables=missing_tables)
        created = [t.name for t in missing_tables]

    # 2) 补缺失列（只针对已存在表）
    for name, table in Base.metadata.tables.items():
        if name in created or name not in existing:
            continue
        have = {c["name"] for c in inspect(conn).get_columns(name)}
        for col in table.columns:
            if col.name in have:
                continue
            if col.primary_key:
                continue
            try:
                ddl_type = col.type.compile(dialect=engine.dialect)
            except Exception:
                ddl_type = "TEXT"
            d = default_for(col)
            sql = f'ALTER TABLE "{name}" ADD COLUMN "{col.name}" {ddl_type}'
            if d is not None:
                sql += f" DEFAULT {d}"
            conn.execute(text(sql))
            added_cols.append(f"{name}.{col.name}")

print("新建表:", created or "无")
print("新增列:", added_cols or "无")
