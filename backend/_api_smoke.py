import json
import sqlite3
import urllib.request

BASE = "http://127.0.0.1:8000/api"


def post(path, body):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())


def get(path, token):
    req = urllib.request.Request(BASE + path, headers={"Authorization": "Bearer " + token})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())


c = sqlite3.connect("foodai.db")
users = c.execute("select id, username from users order by id").fetchall()
print("用户:", users)

picked = None
for uid, name in users:
    for pwd in ("test123456", "123456", "sgx123456"):
        try:
            data = post("/auth/login", {"username": name, "password": pwd})
            picked = (name, pwd, data)
            break
        except Exception as e:
            continue
    if picked:
        break

if not picked:
    print("没有可登录的测试账号，跳过接口测试")
    raise SystemExit

name, pwd, data = picked
token = data["token"]
print("登录成功:", name)

for path in ["/stats/overview", "/wellness/day", "/workouts", "/workouts/stats", "/auth/face/me", "/profile", "/achievements", "/points/summary"]:
    try:
        r = get(path, token)
        s = json.dumps(r, ensure_ascii=False)[:110]
        print("OK  ", path, "->", s)
    except Exception as e:
        body = ""
        if hasattr(e, "read"):
            body = e.read().decode()[:160]
        print("FAIL", path, "->", getattr(e, "code", ""), e, body)
