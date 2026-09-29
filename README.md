# 识膳智行 · AI 健康管理平台

一个"拍照识食物 + AI 定制养生方案 + 打卡积分"的全栈项目。
后端 FastAPI + SQLite，前端 Vue3 + Element Plus，大模型支持**云端 / 本地一键切换**，**默认零费用可跑通全部功能**。

## 功能一览

| 模块 | 说明 |
|---|---|
| 📷 食物识别 | 上传图片 → 视觉模型认菜估重 → 本地营养库算营养 → 保存为饮食记录 |
| ✨ 养生方案 | 偏好勾选 + 文字对话 → 逐天流式生成（带进度）→ 可视化编辑 → 导入日历 |
| ✅ 健康打卡 | 日历任务（餐食 / 运动）+ 完成度积分 + 当日全清奖励 |
| 📊 数据追踪 | 热量趋势 / 体重轨迹 / 营养结构（ECharts 图表） |
| 🏆 排行榜 | 积分实时排名 + 头衔徽章展示 |
| 🛍 积分商城 | 兑换权益 / 实物券，核销码，库存扣减 |
| 👤 健康档案 | 身高体重 / BMI / 热量目标 / 口味偏好 / 饮食禁忌 / 体重记录 |
| 🏠 仪表盘 | 今日概览：热量 / 排名 / 连续打卡天数 |

## 技术栈

- **后端**：FastAPI · SQLAlchemy 2 · Alembic · SQLite · PyJWT（pbkdf2 密码哈希）· openai SDK · Pillow
- **前端**：Vue 3 · Vite · TypeScript · Element Plus · ECharts · Pinia · Vue Router · axios
- **大模型**：OpenAI 兼容协议（云端智谱免费档 / 本地 Ollama / Mock 兜底三层架构）

## 架构

```
Vue3 前端 (5173)
   │  /api 代理
   ▼
FastAPI 后端 (8000) ── SQLite (backend/foodai.db) + uploads/ 图片
   │
   ▼  LLM 抽象层（provider.py 工厂，业务代码零感知）
   ├── OpenAICompatProvider ── 云端：智谱 glm-4-flash / glm-4v-flash（免费档）
   │                       └─ 本地：Ollama qwen2.5:7b-instruct（仅文字）
   └── MockProvider（零依赖模拟；真模型失败自动降级，结果如实标注 provider=mock）
```

关键设计：**视觉模型只负责"认菜 + 估克重"，营养值由本地营养库（60 道菜 JSON）计算**——口径统一、免费小模型也够用。

## 快速开始

### 1. 后端

```bash
cd backend
# 首次：创建虚拟环境 + 装依赖（已有 .venv 可跳过）
E:/app/miniconda3/python.exe -m venv .venv
./.venv/Scripts/python.exe -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 首次：建数据库表
./.venv/Scripts/python.exe -m alembic upgrade head

# 启动（之后开发只需这一条）
./.venv/Scripts/python.exe -m uvicorn app.main:app --port 8000
```

- 接口文档：http://localhost:8000/docs
- 库位置固定为 `backend/foodai.db`（绝对路径，从哪启动都指同一个库）

### 2. 前端

```bash
cd frontend
npm install
npm run dev
```

打开 http://localhost:5173（已配置 `/api`、`/uploads` 代理到 8000，免跨域）

### 3.（可选）本地大模型

```powershell
# 启动 Ollama（模型目录/上下文/常驻显存参数已配好）
.\scripts\start-ollama.ps1

# 首次拉取模型（约 4.7GB，存项目内 models/）
ollama pull qwen2.5:7b-instruct
```

## 大模型配置（`backend/.env`）

| 配置 | 说明 |
|---|---|
| `LLM_MODE=cloud` | 文字 + 视觉都走云端（智谱，https://open.bigmodel.cn 免费申请 key） |
| `LLM_MODE=local` | 文字走本地 Ollama，**视觉仍走云端**（图片识别不占本机显存） |
| `CLOUD_API_KEY` / `CLOUD_TEXT_MODEL` / `CLOUD_VISION_MODEL` | 云端 key 与模型名 |
| `LOCAL_TEXT_BASE_URL` / `LOCAL_TEXT_MODEL` | 本地 Ollama 地址与模型 |
| `LLM_TIMEOUT` | 单次调用超时（秒），超限立即降级 mock |
| `LLM_MAX_RETRIES` | JSON 校验失败重试次数（0 = 失败即兜底，先保响应速度） |
| `LLM_DAY_MAX_TOKENS` / `LLM_CHAT_MAX_TOKENS` / `LLM_VISION_MAX_TOKENS` | 各任务输出上限（防截断、防跑飞） |
| `LLM_FALLBACK_TO_MOCK` | 真模型失败自动降级 mock（演示保命开关） |

说明：

- **零费用原则**：云端只用免费档，本地完全离线；SDK 隐藏重试已关闭，最坏等待 ≈ 超时 × (重试+1)，超限立即兜底，不会长时间卡住。
- **兜底如实标注**：降级 mock 时，识别结果 / 方案记录里 `provider` 字段会写 `mock`，前端显示"本地模拟"，不会拿模拟结果冒充模型结果。
- 首次克隆没有 `.env` 时自动进入 mock 模式，零 key 跑通全部功能。

### 大模型基准测速

```bash
cd backend
./.venv/Scripts/python.exe bench_llm.py          # 按 .env 当前模式
./.venv/Scripts/python.exe bench_llm.py local    # 强制本地
./.venv/Scripts/python.exe bench_llm.py cloud    # 强制云端
```

实测参考（RTX 4060 Laptop 8G / 智谱免费档，本机环境）：

| 用例 | 本地 7B | 云端免费档 |
|---|---|---|
| 聊天首字延迟 | ~0.06s | ~1.3s |
| 生成 1 天方案 | ~5s | ~17.5s |
| 食物识别 | ~3–6s（走云端视觉，两种模式相同） | ~3–6s |

> 本机实测本地模型明显更快且零费用；换机器请以 `bench_llm.py` 实测为准。

## 目录结构

```
foodai/
├── backend/                 # FastAPI 后端（详见 backend/README.md）
│   ├── .env                 # ★ 所有配置（大模型开关/key/超时参数），不入库
│   ├── requirements.txt
│   ├── e2e_test.py          # 端到端自测（29 项断言）
│   ├── bench_llm.py         # 大模型基准测速
│   ├── seed_demo.py         # 排行榜演示数据
│   ├── foodai.db            # SQLite（首次迁移生成，不入库）
│   ├── uploads/             # 上传图片（不入库）
│   ├── alembic/             # 数据库迁移
│   └── app/
│       ├── main.py          # 入口：挂路由 / 种子数据 / LLM 状态接口
│       ├── core/            # 配置 / JWT / 依赖
│       ├── db/              # 数据库连接
│       ├── models/          # 11 张表
│       ├── schemas/         # 请求参数校验
│       ├── routers/         # API 路由（识别/方案/打卡/统计/积分/商城…）
│       ├── services/        # 营养计算 / 积分规则 / 图片压缩 / llm 抽象层
│       └── data/            # foods_data.json 营养库（60 道菜）
├── frontend/                # Vue3 前端（详见 frontend/README.md）
│   └── src/
│       ├── views/           # 10 个页面（一页一个文件）
│       ├── api/             # 类型化接口封装（含 SSE 流式）
│       ├── stores/          # Pinia 状态
│       ├── router/          # 路由表
│       ├── layouts/         # 侧边栏布局
│       └── components/      # 通用组件（图表/统计卡）
├── scripts/
│   ├── start-ollama.ps1     # 启动本地模型服务
│   └── ui_test.py           # Playwright UI 全流程走查（截图到 scripts/shots/）
├── models/                  # Ollama 模型文件（不入库）
└── tools/                   # cloudflared 等工具
```

## 测试与演示脚本

| 命令 | 用途 | 前提 |
|---|---|---|
| `./.venv/Scripts/python.exe e2e_test.py` | API 全链路 29 项断言（注册→识别→方案→打卡→商城） | 后端已启动 |
| `./.venv/Scripts/python.exe bench_llm.py` | 大模型基准测速 | 无需后端 |
| `./.venv/Scripts/python.exe seed_demo.py` | 造排行榜演示用户（可重复执行） | 数据库已建表 |
| `E:/app/miniconda3/python.exe scripts/ui_test.py` | 浏览器全流程走查 + 截图 | 前/后端已启动，已装 Playwright |

## 积分规则

完善档案 +20（一次）· 保存识别 +2 · 生成方案 +10 · 记体重 +2/日 · 方案任务 +3/项 · 当日全清 +10 · 手动打卡 +2/日

## 常见修改速查

| 你想… | 去哪改 |
|---|---|
| 换大模型 / 切云端本地 | `backend/.env`（改完重启） |
| 调教识别 / 方案效果 | `backend/app/services/llm/prompts.py` |
| 给营养库加菜 | `backend/app/data/foods_data.json` |
| 改积分数值 | `backend/app/services/points.py` 顶部 RULES 表 |
| 加商城奖励 | `backend/app/main.py` 的 SEED_REWARDS |
| 加新 API / 新页面 | 见 `backend/README.md` / `frontend/README.md` 的"去哪改"表 |

## 演示账号

`seed_demo.py` 预置 5 个排行榜用户（命中不同积分段），密码均为 `demo123456`：
`demo_xiaoming` / `demo_xiaohong` / `demo_fitness` / `demo_yoga` / `demo_newbie`

## 注意事项

- `.env`、`foodai.db`、`uploads/`、`models/` 均已加入 `.gitignore`，不会入库
- 演示环境建议数据库留空或用 `seed_demo.py` 造数；生产部署前务必修改 `JWT_SECRET`
- 更细的改动指南：[backend/README.md](backend/README.md) · [frontend/README.md](frontend/README.md)
