import json, os, shutil

REPO = r"C:\Users\43120\Desktop\foodai"
THEIRS = r"C:\Users\43120\Desktop\foodai\_remote_main\foodai-main"

rep = json.load(open(os.path.join(REPO, "_merge_report.json"), encoding="utf-8"))
targets = rep["conflict"] + rep["both_added_conflict"]

for i, rel in enumerate(sorted(set(targets))):
    src = os.path.join(THEIRS, rel)
    dst = os.path.join(REPO, rel)
    if os.path.exists(src):
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(src, dst)
        print("copy theirs:", rel)

# 清理冲突/备份临时文件（.conflict 已人工决策为采用组长版）
for root, dirs, files in os.walk(REPO):
    dirs[:] = [d for d in dirs if d not in {"node_modules", ".venv", ".git", "_remote_main", "__pycache__", "dist"}]
    for f in files:
        if f.endswith(".conflict"):
            os.remove(os.path.join(root, f))
            print("rm conflict:", os.path.relpath(os.path.join(root, f), REPO))
print("done")
