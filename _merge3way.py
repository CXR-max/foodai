import subprocess, os, sys, shutil, json

REPO = r"C:\Users\43120\Desktop\foodai"
THEIRS_DIR = r"C:\Users\43120\Desktop\foodai\_remote_main\foodai-main"
BASE_REF = "dae1ce2"
GIT = r"C:\Program Files\Git\cmd\git.exe"

SKIP_DIRS = {"node_modules", ".venv", "__pycache__", "dist", "uploads", ".git"}
SKIP_NAMES = {"_remote_main", "_remote_main.zip"}

def git(*args):
    r = subprocess.run([GIT, "-C", REPO] + list(args), capture_output=True)
    return r.returncode, r.stdout, r.stderr

def git_show(ref, path):
    rc, out, err = git("show", f"{ref}:{path}")
    return out if rc == 0 else None

def tracked(ref):
    rc, out, _ = git("ls-tree", "-r", "--name-only", ref)
    return set(p for p in out.decode("utf-8", "replace").split("\n") if p)

base_files = tracked(BASE_REF)
head_files = tracked("HEAD")

theirs_files = set()
for root, dirs, files in os.walk(THEIRS_DIR):
    dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
    for f in files:
        full = os.path.join(root, f)
        rel = os.path.relpath(full, THEIRS_DIR).replace("\\", "/")
        if f in SKIP_NAMES or rel in SKIP_NAMES:
            continue
        if os.path.getsize(full) > 8 * 1024 * 1024:
            continue
        theirs_files.add(rel)

report = {"from_remote_new": [], "merged_clean": [], "conflict": [], "keep_local_modified": [],
          "deleted": [], "both_added_conflict": [], "ours_deleted_keep": [], "local_only": []}

def read_disk(rel):
    p = os.path.join(REPO, rel)
    if os.path.exists(p):
        with open(p, "rb") as f:
            return f.read()
    return None

def write_disk(rel, data):
    p = os.path.join(REPO, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(data)

union = sorted(base_files | head_files | theirs_files)
for rel in union:
    b = git_show(BASE_REF, rel) if rel in base_files else None
    o = read_disk(rel)
    if o is None and rel in head_files:
        rc, out, _ = git("show", f"HEAD:{rel}")
        o = out if rc == 0 else None
    t = None
    tp = os.path.join(THEIRS_DIR, rel)
    if os.path.exists(tp):
        with open(tp, "rb") as f:
            t = f.read()

    # 1. 双方都没有 -> 跳过
    if t is None and o is None:
        continue

    # 2. 远程没有这个文件
    if t is None:
        if b is None:
            report["local_only"].append(rel)
            continue
        if o is None:
            # 本地删除且远程未改 -> 保持删除
            report["deleted"].append(rel)
            os.remove(os.path.join(REPO, rel)) if os.path.exists(os.path.join(REPO, rel)) else None
            continue
        if o == b:
            report["deleted"].append(rel)
            os.remove(os.path.join(REPO, rel))
        else:
            report["keep_local_modified"].append(rel)
        continue

    # 3. 远程有，本地没有
    if o is None:
        if b is None or t != b:
            write_disk(rel, t)
            report["from_remote_new"].append(rel)
        else:
            pass  # 本地删除且远程未改，保持删除
        continue

    # 4. 双方都有
    if b is None:
        if o == t:
            continue
        # 双方各自新增同名文件 -> 冲突：以远程为准，本地留 .local 备份
        write_disk(rel + ".local", o)
        write_disk(rel, t)
        report["both_added_conflict"].append(rel)
        continue

    if o == t:
        continue
    if o == b:
        write_disk(rel, t)
        report["from_remote_new"].append(rel)
        continue
    if t == b:
        report["keep_local_modified"].append(rel)
        continue

    # 真三方合并
    tmpdir = os.path.join(REPO, "_merge_tmp")
    os.makedirs(tmpdir, exist_ok=True)
    bo = os.path.join(tmpdir, "base")
    oo = os.path.join(tmpdir, "ours")
    to = os.path.join(tmpdir, "theirs")
    with open(bo, "wb") as f: f.write(b)
    with open(oo, "wb") as f: f.write(o)
    with open(to, "wb") as f: f.write(t)
    r = subprocess.run([GIT, "merge-file", "-p", oo, bo, to], capture_output=True)
    if r.returncode == 0:
        write_disk(rel, r.stdout)
        report["merged_clean"].append(rel)
    elif r.returncode > 0:
        write_disk(rel + ".conflict", r.stdout)
        report["conflict"].append(rel)
    else:
        write_disk(rel + ".conflict", r.stdout or b"")
        report["conflict"].append(rel)

shutil.rmtree(os.path.join(REPO, "_merge_tmp"), ignore_errors=True)
with open(os.path.join(REPO, "_merge_report.json"), "w", encoding="utf-8") as f:
    json.dump(report, f, ensure_ascii=False, indent=2)

for k, v in report.items():
    print(f"\n=== {k} ({len(v)}) ===")
    for x in v:
        print("  " + x)
