import sqlite3, os

db_path = r"C:\Users\43120\Desktop\foodai\backend\foodai.db"

# 第一次连接：加列
conn1 = sqlite3.connect(db_path)
cur1 = conn1.cursor()
cur1.execute("ALTER TABLE health_profiles ADD COLUMN body_fat_pct FLOAT")
conn1.commit()
conn1.close()
print("第一次连接：ALTER + commit + close")

# 第二次连接：重新查
conn2 = sqlite3.connect(db_path)
cur2 = conn2.cursor()
cur2.execute("PRAGMA table_info(health_profiles)")
cols = [r[1] for r in cur2.fetchall()]
print(f"第二次连接查到列: {cols}")
print(f"body_fat_pct 在列中: {'body_fat_pct' in cols}")

# 再加 check_ins 的列
cur2.execute("ALTER TABLE check_ins ADD COLUMN intensity VARCHAR(20)")
cur2.execute("ALTER TABLE check_ins ADD COLUMN met_used FLOAT")
cur2.execute("ALTER TABLE check_ins ADD COLUMN burn_detail JSON")
conn2.commit()
conn2.close()

# 第三次连接验证
conn3 = sqlite3.connect(db_path)
cur3 = conn3.cursor()
cur3.execute("PRAGMA table_info(check_ins)")
ci_cols = [r[1] for r in cur3.fetchall()]
print(f"\ncheck_ins 列: {ci_cols}")
conn3.close()
print("完成！")
