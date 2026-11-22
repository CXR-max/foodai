import sys, os
os.chdir(r"C:\Users\43120\Desktop\foodai\backend")
sys.path.insert(0, ".")

from app.core.config import settings
from app.db.session import engine
from sqlalchemy import inspect

print(f"DATABASE_URL: {settings.DATABASE_URL}")
print(f"engine.url: {engine.url}")

# 实际连接的数据库文件
print(f"\n数据库文件: {engine.url.database}")
print(f"文件存在: {os.path.exists(engine.url.database)}")
print(f"文件大小: {os.path.getsize(engine.url.database)} bytes")

# 用 inspect 看实际有哪些表
insp = inspect(engine)
tables = insp.get_table_names()
print(f"\n实际表数: {len(tables)}")
for t in sorted(tables):
    print(f"  {t}")
