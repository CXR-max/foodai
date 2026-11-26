"""创建领地表并铺学校操场演示数据（不影响其他表）

用法：python seed_territory.py --reset
"""
import sys

from sqlalchemy import text

from app.db.session import Base, engine, SessionLocal
from app.models.territory import TerritoryCell, TerritoryEvent, TerritoryPref, TerritoryRun, UserFriend
from app.services.territory import seed_demo

Base.metadata.create_all(bind=engine, tables=[
    TerritoryCell.__table__, TerritoryRun.__table__,
    TerritoryPref.__table__, TerritoryEvent.__table__, UserFriend.__table__,
])

# 老库补 color 列（create_all 不会改已存在的表）
with engine.begin() as conn:
    cols = [r[1] for r in conn.execute(text("PRAGMA table_info(territory_prefs)"))]
    if cols and "color" not in cols:
        conn.execute(text("ALTER TABLE territory_prefs ADD COLUMN color VARCHAR(16)"))
    run_cols = [r[1] for r in conn.execute(text("PRAGMA table_info(territory_runs)"))]
    if run_cols and "cells_cut" not in run_cols:
        conn.execute(text("ALTER TABLE territory_runs ADD COLUMN cells_cut INTEGER DEFAULT 0"))

db = SessionLocal()
try:
    result = seed_demo(db, reset="--reset" in sys.argv)
    print("seed:", result)
    print("cells:", db.query(TerritoryCell).count())
    print("friends:", db.query(UserFriend).count())
finally:
    db.close()
