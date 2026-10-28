# 识膳智行 · AI 健康管理平台

拍照识食物 + AI 定制养生方案 + 打卡积分。后端 FastAPI + SQLite，前端 Vue 3 + Element Plus，
大模型支持**云端 / 本地一键切换**，默认零费用可跑通全部功能。

## 功能

| 模块 | 说明 |
|---|---|
| 📷 食物识别 | 图片真解码校验 → 视觉模型逐道认菜估重 → 本地营养库算营养 → 多菜保存为多条记录；匹配方式可解释（精确/同义/别名/相似），低置信度弹候选让用户确认 |
| ✨ 养生方案 | 偏好勾选 + 持久化对话 → 本地 Qwen 抽取约束 → 约束求解器逐天流式生成 → 可视化编辑 → 导入日历 |
| ✅ 健康打卡 | 日历任务（餐食 / 运动）+ 完成度积分 + 当日全清奖励 |
| 📊 数据追踪 | 热量趋势 / 体重轨迹 / 营养结构（ECharts） |
| 🏆 排行榜 | 积分实时排名（Redis ZSet，降级 SQLite）+ 头衔徽章 |
| 🛍 积分商城 | 权益 / 实物券，核销码，库存扣减 |
| 👤 健康档案 | 身高体重 / BMI / 热量目标 / 口味偏好 / 饮食禁忌 / 体重记录 |
| 🏠 仪表盘 | 今日概览：热量 / 排名 / 连续打卡 |

识别页还提供：今日热量预算条、禁忌/过敏提示（按档案 15 项规则）、份量快捷（小/标准/大份）、
识别历史分页/重跑/删除、多菜逐道校准。

## 技术栈

- **后端**：FastAPI · SQLAlchemy 2 · Alembic · SQLite · PyJWT · openai SDK · Pillow · openpyxl
- **前端**：Vue 3 · Vite · TypeScript · Element Plus · ECharts · Pinia · Vue Router · axios
- **大模型**：OpenAI 兼容协议；文字可走本地 Ollama（qwen2.5:7b-instruct）或云端，视觉走云端，失败自动降级 Mock

## 架构

```
Vue3 前端 (5173)
   │  /api 代理
   ▼
FastAPI 后端 (8000) ── SQLite (backend/foodai.db) + uploads/ 图片
   │
   ▼  LLM 抽象层（provider.py 工厂，业务代码零感知）
   ├── OpenAICompatProvider ── 文字：本地 Ollama / 云端智谱
   │                        └─ 视觉：云端 glm-4v-flash（429 时切备用模型）
   └── MockProvider（零依赖模拟；真模型失败自动降级，provider 字段如实标注 mock）
```

关键设计：**视觉模型只负责「认菜 + 判份量档位」，克重由本地标准份量校准、营养值由本地营养库计算**。
口径统一，免费小模型也够用；识别错了可在识别页手动校准（只改菜名 + 克数，营养仍由本地库重算）。

## 快速开始

### 1. 后端（一键建库 + 启动）

```powershell
cd backend
# 首次：创建虚拟环境并装依赖（已有 .venv 可跳过）
E:/app/miniconda3/python.exe -m venv .venv
./.venv/Scripts/python.exe -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 首次：迁移 + 种子 + 200 道菜谱 + 激活默认菜系
./.venv/Scripts/python.exe seed_all.py

# 启动
./.venv/Scripts/python.exe -m uvicorn app.main:app --reload --port 8000
```

- 接口文档：<http://localhost:8000/docs>
- 数据库固定在 `backend/foodai.db`

### 2. 前端

```powershell
cd frontend
npm install
npm run dev
```

打开 <http://127.0.0.1:5173>（`/api`、`/uploads` 已代理到 8000，免跨域）。

### 3. 本地文字模型（可选，默认推荐）

```powershell
# 模型放在仓库根目录 models/（已 gitignore），脚本会自动指向
.\scripts\start-ollama.ps1
```

Ollama 没启动时，文字请求会自动切云端（需要 `CLOUD_API_KEY`），不会直接挂掉。

### 4. 一键启动（VSCode）

仓库根目录 `.vscode/` 已配置调试与任务：`Ctrl+Shift+P` → **Tasks: Run Task** → **全栈: 一键启动**。

## 模型配置（`backend/.env`，不提交）

**切换统一用项目自带脚本，不要手改 `.env`**：

```powershell
cd backend
.\.venv\Scripts\python.exe switch_llm.py status    # 看当前配置
.\.venv\Scripts\python.exe switch_llm.py local     # 文字本地千问 + 视觉云端（推荐）
.\.venv\Scripts\python.exe switch_llm.py zhipu     # 全云端（智谱免费档）
.\.venv\Scripts\python.exe switch_llm.py mock      # 纯模拟，零费用
```

| 配置 | 作用 |
|---|---|
| `TEXT_PROVIDER=local/glm/qwen` | **决定文字走哪家**（只改 `LLM_MODE` 不生效） |
| `LLM_MODE=local` | 本地模式标记；文字走 Ollama，视觉仍走云端 |
| `CLOUD_VISION_MODEL` / `CLOUD_VISION_FALLBACK_MODEL` | 视觉主模型 + 限流备用模型 |
| `LLM_VISION_MAX_TOKENS` | 视觉输出上限，`glm-4v-flash` 硬上限 1024 |
| `TEXT_FALLBACK_TO_CLOUD` | 本地模型连不上时自动切云端 |
| `VISION_DAILY_QUOTA` / `VISION_DEDUPE_HOURS` | 每人每日识别上限、同图去重窗口 |

> 改 `.env` 后必须重启 uvicorn；配置只在进程启动时读取。

## 机密信息（不要提交）

- `backend/.env`（含 `CLOUD_API_KEY` / `ZHIPU_API_KEY` / `QWEN_API_KEY` / `JWT_SECRET`）**已在 `.gitignore`**，只放本地；
- 证书、`*.key`、`*.pem`、`auth.json`、`credentials*.json` 同样被忽略；
- 仓库自带提交前扫描，命中疑似 key 会**阻止提交**：

```powershell
# 每个克隆只需执行一次（启用版本库里的钩子）
git config core.hooksPath scripts/git-hooks

# 手动全量检查
python scripts/check_secrets.py
```

**万一误提交了 key**：`git rm --cached <文件>` → 立刻去平台**作废并重新生成**该 key（历史里的 key 已经泄露，改代码不够）→ 再提交。

## 营养库

| 口径 | 数量 |
|---|---|
| 活跃池（识别可命中） | **1,847** |
| staging（未激活，含四个外部源） | 11,616 |
| 总条目 | 13,463 |
| 同义/易混淆关系 | 34 组 / 58 条 |
| 别名 | 5,276 条 |

- 数据来源与许可、导入命令、扩容流程：[backend/app/data/SOURCES.md](backend/app/data/SOURCES.md)
- 200 道菜谱的营养预览：[docs/recipes-preview-200.md](docs/recipes-preview-200.md)

## 目录结构

```
foodai/
├── backend/
│   ├── app/
│   │   ├── main.py          # 入口：挂路由 / 种子 / LLM 状态
│   │   ├── core/            # 配置 / JWT / 依赖
│   │   ├── models/          # 18 张表（业务 11 + 营养库 7）
│   │   ├── routers/         # 识别/方案/打卡/统计/积分/商城/成就…
│   │   ├── services/        # 营养计算 / 识别匹配 / 积分 / LLM 抽象层
│   │   └── data/            # 种子 JSON / 菜谱 / 关系表 / SOURCES.md
│   ├── scripts/nutrition_import/   # 导入与数据治理工具
│   ├── alembic/             # 数据库迁移
│   └── seed_all.py          # 一键建库
├── frontend/src/            # Vue 3 前端
├── scripts/                 # 启动/停止/截图等辅助脚本
├── docs/                    # 部署说明、数据预览
└── data_sources/            # 外部原始数据（gitignore）
```

## 验证

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest tests -q      # 38 个用例
cd ../frontend
npm run build                                       # 类型检查 + 产物构建
```

## 文档索引

| 文档 | 内容 |
|---|---|
| [backend/README.md](backend/README.md) | 后端结构、接口清单、脚本说明 |
| [frontend/README.md](frontend/README.md) | 前端结构、页面与组件约定 |
| [backend/app/data/SOURCES.md](backend/app/data/SOURCES.md) | 营养数据来源、许可、导入与扩容流程 |
| [docs/deployment.md](docs/deployment.md) | 本机部署、端口、常见问题 |
| [docs/recipes-preview-200.md](docs/recipes-preview-200.md) | 200 道菜谱营养预览 |

## 已知限制

- 菜谱营养是「食材克重 × 食材营养」的估算（`verified=false`），非实验室检测值；
- 视觉识别依赖云端免费模型，高峰期可能 429，会切备用模型或降级 mock（页面会标注）；
- 外部数据（USDA/CoFID/MEXT）是英文/日文名，主要作食材原料与校准基准，未直接参与中文菜名匹配。
