import sys, os
os.chdir(r"C:\Users\43120\Desktop\foodai\backend")
sys.path.insert(0, ".")

from sqlalchemy.schema import CreateTable
from sqlalchemy import text
from app.db.session import Base, engine
from app.models import *  # noqa

# 打印 foods 表的 DDL
foods_tbl = Base.metadata.tables["foods"]
ddl = str(CreateTable(foods_tbl).compile(engine))
with open(r"C:\Users\43120\Desktop\foodai\_ddl.txt", "w", encoding="utf-8") as f:
    f.write("=== foods DDL ===\n")
    f.write(ddl)
    f.write("\n\n=== 直接执行 ===\n")
    try:
        with engine.begin() as conn:
            conn.execute(text(ddl))
            f.write("OK: foods 表创建成功\n")
    except Exception as e:
        f.write(f"FAIL: {e}\n")
