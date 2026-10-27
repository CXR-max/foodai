# 识膳智行 后端（FastAPI + SQLite）

AI 健康管理平台后端。**默认 mock 模式，无需任何 API key 即可跑通全部功能**。

## 启动（3 条命令）

```bash
# 1. 首次：创建虚拟环境并装依赖（已有 .venv 跳过）
E:/app/miniconda3/python.exe -m venv .venv
./.venv/Scripts/python.exe -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 2. 首次：建数据库表
./.venv/Scripts/python.exe -m alembic upgrade head

# 3. 启动（之后每天开发只需这一条）
./.venv/Scripts/python.exe -m uvicorn app.main:app --reload --port 8000
```

- 接口文档：http://localhost:8000/docs
- 演示造数（排行榜预置几个用户）：`./.venv/Scripts/python.exe seed_demo.py`
- 端到端自测（需先启动）：`./.venv/Scripts/python.exe e2e_test.py`

## 目录结构（一个功能 = 一个文件，好找好改）

```
backend/
├── .env                       # ★ 所有配置在这（大模型开关/key/数据库）
├── requirements.txt           # 依赖清单
├── seed_demo.py               # 比赛演示造数脚本
├── e2e_test.py                # 端到端测试（29 项断言）
├── bench_llm.py               # 大模型基准测速（本地/云端对比）
├── foodai.db                  # SQLite 数据库文件（首次迁移后生成）
├── uploads/                   # 上传的食物图片
├── alembic/                   # 数据库迁移
└── app/
    ├── main.py                # 入口：创建应用/挂路由/商城奖励种子数据
    ├── core/
    │   ├── config.py          # 配置读取（.env）
    │   ├── security.py        # 密码哈希(pbkdf2) + JWT
    │   └── deps.py            # "当前登录用户"依赖
    ├── db/session.py          # 数据库连接
    ├── models/                # ★ 11 张表定义（users/health_profiles/recognitions/
    │                          #   food_records/diet_plans+plan_days/check_ins/
    │                          #   points_logs/weight_logs/rewards/redemptions）
    ├── schemas/               # API 请求参数校验
    ├── routers/               # ★ API 路由，按模块一个文件：
    │                          #   auth 认证 / profile 档案 / recognitions 识别 /
    │                          #   food_records 饮食 / plans 方案 / checkins 打卡 /
    │                          #   stats 统计 / points 积分排行 / rewards 商城
    ├── services/
    │   ├── nutrition.py       # ★ BMI/BMR 计算 + 营养库匹配换算（识别管线枢纽）
    │   ├── points.py          # ★ 积分规则（改积分数值就改这张表）
    │   ├── images.py          # 识别前图片压缩（长边≤1024 转 JPEG，显著提速）
    │   └── llm/               # ★ 大模型抽象层
    │       ├── provider.py    #   工厂：根据 .env 决定用 mock 还是真模型（含降级）
    │       ├── mock_provider.py#  免 key 模拟（同图同结果，演示可复现）
    │       ├── openai_compat.py#  真模型（openai 兼容协议，默认智谱免费档）
    │       ├── prompts.py     #   两段提示词（调教 AI 效果改这里）
    │       ├── base.py        #   JSON 校验+自动重试框架
    │       ├── schemas.py     #   模型输出结构定义
    │       └── foods_data.py  #   营养库加载/模糊匹配/换算
    └── data/
        └── foods_data.json    # ★ 60 道菜营养库（加菜编辑这个文件，不用碰代码）
```

## 常见修改"去哪改"速查表

| 你想… | 去哪改 |
|---|---|
| **改积分数值/加积分规则** | `app/services/points.py` 顶部 `RULES` 表 |
| **接真实大模型（免费）** | `.env`：`LLM_MODE=cloud` + 填 `CLOUD_API_KEY`（智谱 open.bigmodel.cn 免费申请）；或 `LLM_MODE=local` 用本机 Ollama；重启即可 |
| **换别的厂商模型** | `.env` 改 `CLOUD_BASE_URL` + `CLOUD_TEXT_MODEL`（如硅基流动/通义），业务代码零改动 |
| **调响应速度 / 超时兜底** | `.env`：`LLM_TIMEOUT` / `LLM_MAX_RETRIES` / `LLM_*_MAX_TOKENS`；改完用 `bench_llm.py` 实测 |
| **调教 AI 识别/方案效果** | `app/services/llm/prompts.py` |
| **给营养库加菜** | `app/data/foods_data.json`，复制一条现有记录改名字和 per_100g 四个数字 |
| **给商城加奖励** | `app/main.py` 里 `SEED_REWARDS` 列表，删库重建或手动插一条 rewards |
| **数据库加字段** | `app/models/` 对应表加一列 → `python -m alembic revision --autogenerate -m "xx"` → `python -m alembic upgrade head` |
| **加新 API** | `app/routers/` 建文件，`main.py` 里 include 一行 |

## 大模型两层架构（重要设计）

```
业务代码 → 只认 LLMProvider 统一接口（provider.py 工厂）
              ├── MockProvider        ← 未配置 key 时（零 key 零成本）
              └── OpenAICompatProvider ← LLM_MODE=cloud（云端智谱）/ local（本地 Ollama）
                    └── 失败自动降级回 Mock（LLM_FALLBACK_TO_MOCK=true，结果如实标注 provider=mock）
```

调用治理：SDK 隐藏重试已关闭 + `LLM_TIMEOUT` + `LLM_MAX_RETRIES`，最坏等待 ≈ 超时 ×(重试+1)，超限立即降级 mock；方案逐天流式生成并推送进度，单天输出有上限防截断。

识别管线：**视觉模型只负责"认菜+估克重"，营养值由本地营养库计算**（口径统一，免费小模型也够用）；图片上传后先压缩（长边≤1024、JPEG）再送视觉模型。

## 积分规则（当前值）

完善档案 +20(一次) · 保存识别 +2 · 生成方案 +10 · 记体重 +2/日 · 方案任务 +3/项 · 当日全清 +10 · 手动打卡 +2/日
