import json
import re
import urllib.request

BASE = "https://separated-measure-conflicts-herbal.trycloudflare.com"
html = urllib.request.urlopen(BASE + "/", timeout=30).read().decode("utf-8")
print("html", len(html))
print(re.findall(r'(?:src|href)="([^"]+)"', html)[:6])
for p in re.findall(r'(?:src|href)="(/assets/[^"]+)"', html)[:3]:
    r = urllib.request.urlopen(BASE + p, timeout=30)
    b = r.read()
    print(p, r.status, len(b), r.headers.get("content-type"))
r = urllib.request.urlopen(BASE + "/api/health", timeout=30)
print("api", r.status, r.read().decode())
req = urllib.request.Request(
    BASE + "/api/auth/login",
    data=json.dumps({"username": "demo_xiaoming", "password": "demo123456"}).encode(),
    headers={"Content-Type": "application/json"},
    method="POST",
)
r = urllib.request.urlopen(req, timeout=30)
d = json.loads(r.read())
print("login", r.status, d["user"]["username"], len(d["token"]))
