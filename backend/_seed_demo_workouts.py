"""生成演示用的运动记录（供截图 / 演示），走真实接口，因此也会触发打卡联动。"""
import json
import urllib.request
from datetime import datetime, timedelta

BASE = "http://127.0.0.1:8000"
from app.core.security import create_token  # noqa: E402

TOKEN = create_token(1)
HEAD = {"Authorization": f"Bearer {TOKEN}"}


def make_track(lat0, lng0, distance_m, speed_mps=2.9, step_sec=10):
    """沿一条略微弯曲的路线生成轨迹点，间隔 step_sec 秒。"""
    total = int(distance_m / speed_mps)
    pts = []
    lat, lng = lat0, lng0
    n = total // step_sec
    for i in range(n + 1):
        pts.append({
            "lat": round(lat, 6),
            "lng": round(lng, 6),
            "t": i * step_sec,
            "acc": 4,
        })
        lat += (speed_mps * step_sec) / 111320.0
        lng += (speed_mps * step_sec) / 111320.0 * 0.35 * (1 if i % 40 < 20 else -1)
    return pts, total


def post(distance_m, hours_ago, note):
    track, dur = make_track(31.230416, 121.473701, distance_m)
    body = {
        "sport_type": "跑步",
        "source": "gps",
        "start_at": (datetime.now() - timedelta(hours=hours_ago)).isoformat(),
        "duration_sec": dur,
        "track": track,
        "note": note,
    }
    req = urllib.request.Request(
        f"{BASE}/api/workouts",
        data=json.dumps(body).encode("utf-8"),
        headers={**HEAD, "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        code = resp.status
    print(f"{note}: {code} id={data.get('id')} dist={data.get('distance_m')} "
          f"pace={data.get('avg_pace_sec_km')} cal={data.get('calories')}")
    return data.get("id")


ids = []
ids.append(post(3240, 3, "晨跑 · 滨江"))
ids.append(post(5120, 75, "周末拉练"))
print("WORKOUT_IDS=" + json.dumps(ids))
