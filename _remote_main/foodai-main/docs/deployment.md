# 本机部署说明

> 适用：Windows + 本仓库（`E:\allprograms\foodai`）。
> 日常开发/演示用「一键启动」，更细的接口与脚本见 [backend/README.md](../backend/README.md)。

## 一、端口与进程

| 服务 | 端口 | 说明 |
|---|---|---|
| 前端（Vite） | 5173 | 浏览器打开 <http://127.0.0.1:5173> |
| 后端（uvicorn） | 8000 | 接口文档 <http://127.0.0.1:8000/docs> |
| 本地文字模型（Ollama） | 11434 | 仅 `TEXT_PROVIDER=local` 时需要 |

## 二、首次安装

```powershell
# 1) 后端依赖
cd backend
E:/app/miniconda3/python.exe -m venv .venv
./.venv/Scripts/python.exe -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 2) 建库 + 种子 + 200 道菜谱 + 激活默认菜系（幂等，可重复执行）
./.venv/Scripts/python.exe seed_all.py

# 3) 前端依赖
cd ../frontend
npm install

# 4)（可选）本地文字模型：模型放仓库根目录 models/，Ollama 会自动识别
cd ..
.\scripts\start-ollama.ps1
```

## 三、日常启动

**方式 A：双击 `启动.bat`**（自动拉起 Ollama → 后端 → 前端，并打开浏览器）

**方式 B：手动三个终端**

```powershell
# 终端 1：本地模型（可选）
.\scripts\start-ollama.ps1

# 终端 2：后端
cd backend
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000

# 终端 3：前端
cd frontend
npm run dev
```

**方式 C：VSCode**：`Ctrl+Shift+P` → Tasks: Run Task → **全栈: 一键启动**。

停止：双击 `停止.bat`，或直接关掉对应窗口。

## 四、切换模型

统一用项目自带脚本（它会重写 `backend/.env`，不要手改）：

```powershell
cd backend
.\.venv\Scripts\python.exe switch_llm.py status
.\.venv\Scripts\python.exe switch_llm.py local     # 文字本地千问，视觉云端（推荐）
.\.venv\Scripts\python.exe switch_llm.py zhipu     # 全云端
.\.venv\Scripts\python.exe switch_llm.py mock      # 纯模拟，零费用
```

> 关键：文字走哪家由 `TEXT_PROVIDER` 决定，不是 `LLM_MODE`。
> 切完必须重启后端才生效。

## 五、常见问题

| 现象 | 原因 / 处理 |
|---|---|
| 识别页标「本地模拟」 | 视觉模型失败或没配 key。看 `backend/server.err.log`：`429` 是限流（已配备用模型自动切），`max_tokens 非法` 检查 `LLM_VISION_MAX_TOKENS≤1024` |
| 文字回复很慢 | 本地 7B 约 4 秒/次；想快就 `switch_llm.py zhipu` |
| 5173 打不开 | 用 `127.0.0.1` 而不是 `localhost`；确认 `npm run dev` 窗口没关 |
| 8000 被占用 | `netstat -ano | findstr :8000` 找到 PID 后结束，或换端口 |
| Ollama 连不上 | 跑 `.\scripts\start-ollama.ps1`；确认 11434 可访问 |
| 改了 `.env` 没反应 | 配置只在进程启动时读取，重启后端 |
| 迁移报 multiple heads | 仓库已合过合并点；执行 `python -m alembic heads` 确认只有一个 head |
| 数据乱了想重来 | 删掉 `backend/foodai.db`，重新 `python seed_all.py` |

## 六、数据重建

```powershell
cd backend
Remove-Item foodai.db -ErrorAction SilentlyContinue   # 谨慎：会清空本地数据
.\.venv\Scripts\python.exe seed_all.py
```

外部营养数据（TFDA/CoFID/USDA/MEXT）不在仓库里，重建后只有种子 + 菜谱；
需要完整活跃池时按 [SOURCES.md](../backend/app/data/SOURCES.md) 先导入再 `promote`。
