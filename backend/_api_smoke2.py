import json
import urllib.request

BASE = "http://127.0.0.1:8000/api"


def post(path, body):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())


def get(path, token):
    req = urllib.request.Request(BASE + path, headers={"Authorization": "Bearer " + token})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())


data = post("/auth/login", {"username": "solver_b2a4d4a8", "password": "test123456"})
token = data["token"]

for path in ["/day/summary", "/day/state", "/sleep", "/water", "/points/me", "/foods/stats",
             "/stats/weekly-report", "/stats/trends", "/rewards", "/plans/active"]:
    try:
        r = get(path, token)
        print("OK  ", path, "->", json.dumps(r, ensure_ascii=False)[:100])
    except Exception as e:
        body = e.read().decode()[:200] if hasattr(e, "read") else ""
        print("FAIL", path, "->", getattr(e, "code", ""), e, body)
