"""一次性脚本：本地模型启动脚本改名后，修正内部注释与 bat 调用路径。"""
import io
import os

os.chdir(r"D:\foodai")

p = "scripts/start-local-llm.ps1"
s = io.open(p, encoding="utf-8-sig").read()
s = s.replace(r"scripts\start-ollama.ps1", r"scripts\start-local-llm.ps1")
io.open(p, "w", encoding="utf-8-sig", newline="\r\n").write(s)
print("ps1 done")

b = "启动本地模型.bat"
t = io.open(b, encoding="gbk").read()
t = t.replace(r"scripts\start-ollama.ps1", r"scripts\start-local-llm.ps1")
io.open(b, "w", encoding="gbk", newline="\r\n").write(t)
print("bat done:", "start-local-llm.ps1" in t)
