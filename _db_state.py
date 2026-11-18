import sqlite3

c = sqlite3.connect('foodai.db')
ts = [r[0] for r in c.execute("select name from sqlite_master where type='table' order by name")]
print('TABLES:', len(ts))
print(', '.join(ts))
try:
    print('ALEMBIC:', c.execute('select version_num from alembic_version').fetchall())
except Exception as e:
    print('ALEMBIC ERR', e)
for t in ['workouts', 'user_faces', 'foods', 'sleep_logs', 'water_logs', 'weekly_report_cache']:
    print(t, 'EXISTS' if t in ts else 'MISSING')
