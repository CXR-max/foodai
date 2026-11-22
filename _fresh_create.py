import sys, os
os.chdir(r"C:\Users\43120\Desktop\foodai\backend")
sys.path.insert(0, ".")

from app.db.session import Base, engine
from app.models import *  # noqa

print("正在创建所有表...")
try:
    Base.metadata.create_all(bind=engine)
    print("create_all 成功！")
except Exception as e:
    print(f"create_all 失败: {e}")
    # 降级：只建表不建索引
    print("降级：逐表创建...")
    from sqlalchemy.schema import CreateTable
    from sqlalchemy import text
    with engine.begin() as conn:
        for name, table in Base.metadata.tables.items():
            try:
                conn.execute(text(str(CreateTable(table).compile(engine))))
                print(f"  建表: {name}")
            except Exception as e2:
                if "already exists" in str(e2):
                    print(f"  已存在: {name}")
                else:
                    print(f"  失败: {name} - {e2}")

# 验证
from sqlalchemy import inspect
insp = inspect(engine)
tables = insp.get_table_names()
print(f"\n最终表数: {len(tables)}")
for t in sorted(tables):
    print(f"  {t}")
