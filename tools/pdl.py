"""
多线程分段下载器：单连接被镜像限速时，用多连接并行拉取同一个文件
用法：python pdl.py <url> <输出路径> [线程数]
"""
import os
import subprocess
import sys
import threading
import time

url = sys.argv[1]
out = sys.argv[2]
threads = int(sys.argv[3]) if len(sys.argv) > 3 else 8


def head_size(u: str) -> int:
    r = subprocess.run(["curl", "-sIL", "-m", "30", u], capture_output=True, text=True)
    n = 0
    for line in r.stdout.splitlines():
        if line.lower().startswith("content-length:"):
            n = int(line.split(":")[1].strip())
    return n


def grab(start: int, end: int, idx: int, retries: int = 6) -> None:
    part = out + f".part{idx}"
    for attempt in range(retries):
        if os.path.exists(part) and os.path.getsize(part) == end - start + 1:
            break
        subprocess.run(
            ["curl", "-sL", "-m", "900", "--retry", "2",
             "-r", f"{start}-{end}", "-o", part, url],
            capture_output=True,
        )
        if os.path.exists(part) and os.path.getsize(part) == end - start + 1:
            break
        time.sleep(1 + attempt)
    else:
        raise RuntimeError(f"分片 {idx} 下载失败")


size = head_size(url)
if size <= 0:
    raise SystemExit("拿不到文件大小")
print(f"总大小 {size / 1048576:.1f} MB，{threads} 线程分段下载", flush=True)

chunk = size // threads
ranges = []
for i in range(threads):
    s = i * chunk
    e = size - 1 if i == threads - 1 else (i + 1) * chunk - 1
    ranges.append((s, e, i))

# 先并发出第一个分片，确认能跑通，再放开
tor = []
t0 = time.time()
for s, e, i in ranges:
    t = threading.Thread(target=grab, args=(s, e, i), daemon=True)
    t.start()
    tor.append(t)

while any(t.is_alive() for t in tor):
    done = 0
    for _, _, i in ranges:
        p = out + f".part{i}"
        if os.path.exists(p):
            done += os.path.getsize(p)
    speed = done / max(time.time() - t0, 0.1)
    print(f"\r已下载 {done / 1048576:7.1f} / {size / 1048576:.1f} MB  "
          f"速度 {speed / 1048576:.2f} MB/s", end="", flush=True)
    time.sleep(2)

print()
# 合并
with open(out, "wb") as f:
    for _, _, i in ranges:
        p = out + f".part{i}"
        with open(p, "rb") as src:
            while True:
                b = src.read(8 * 1024 * 1024)
                if not b:
                    break
                f.write(b)
        os.remove(p)

print(f"完成，用时 {time.time() - t0:.0f}s，文件大小 {os.path.getsize(out) / 1048576:.1f} MB")
