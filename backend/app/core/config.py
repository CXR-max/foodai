"""
全局配置（读取 backend/.env 文件）
想改配置：直接编辑 backend/.env，不需要动这个文件
"""
from pathlib import Path

from pydantic_settings import BaseSettings

# backend/ 目录的绝对路径（config.py 在 backend/app/core/ 下，往上走 3 级）
BASE_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """所有配置项。.env 里的同名环境变量会自动覆盖这里的默认值"""

    APP_NAME: str = "识膳智行 AI 健康管理平台"

    # ---- JWT 登录凭证 ----
    JWT_SECRET: str = "dev-secret-please-change"   # 生产环境务必改成随机字符串
    JWT_EXPIRE_DAYS: int = 7                        # token 有效期（天）

    # ---- 数据库：固定为 backend/foodai.db 绝对路径 ----
    # 用绝对路径的原因：不管从哪个目录启动 uvicorn/alembic，都指向同一个库文件
    DATABASE_URL: str = f"sqlite+pysqlite:///{(BASE_DIR / 'foodai.db').as_posix()}"

    # ---- 图片上传目录 ----
    UPLOAD_DIR: Path = BASE_DIR / "uploads"

    # ---- Redis 排行榜（可选，工业方案）----
    # 不装 Redis 时自动降级为 SQLite 查询，功能照常；装了 Redis 后 ZSet 承担排序
    REDIS_ENABLED: bool = True          # 是否尝试启用 Redis
    REDIS_HOST: str = "127.0.0.1"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    REDIS_PASSWORD: str = ""            # 本机默认无密码
    REDIS_LEADERBOARD_KEY: str = "foodai:leaderboard"   # ZSet 键名（member=user_id, score=积分）

    # ---- 大模型：模式开关 + 云端/本地两套配置 ----
    # 切模式只改这一行：cloud=文字+视觉都走云端 | local=文字走本地Ollama，视觉仍走云端
    LLM_MODE: str = "cloud"

    # ---- 方案引擎：llm=沿用原大模型/Mock 生成；solver=CP-SAT 约束求解器直接构造 ----
    # 回滚：.env 改回 PLAN_ENGINE=llm 即可（无需改代码）
    PLAN_ENGINE: str = "llm"

    # 云端（文字 + 视觉共用一套；智谱开放平台 https://open.bigmodel.cn 免费申请 key）
    CLOUD_BASE_URL: str = "https://open.bigmodel.cn/api/paas/v4"
    CLOUD_API_KEY: str = ""
    CLOUD_TEXT_MODEL: str = "glm-4-flash"        # 免费、限流宽松
    CLOUD_VISION_MODEL: str = "glm-4v-flash"     # 免费视觉（4V 系列，高峰期限流较轻，识别更稳）
    # 备用视觉模型：主模型持续限流(429)时自动切换；留空则不启用
    CLOUD_VISION_FALLBACK_MODEL: str = ""

    # 本地（Ollama，仅文字；视觉仍走云端）
    LOCAL_TEXT_BASE_URL: str = "http://localhost:11434/v1"
    LOCAL_TEXT_API_KEY: str = "ollama"        # Ollama 不校验，占位即可
    LOCAL_TEXT_MODEL: str = "qwen2.5:7b"

    # 通义千问（云端文字，阿里云 DashScope OpenAI 兼容端点，有免费额度）
    QWEN_BASE_URL: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    QWEN_API_KEY: str = ""
    QWEN_TEXT_MODEL: str = "qwen-plus"

    # 文字模型提供商：glm=智谱云端 | qwen=通义千问云端 | local=本地 Ollama
    TEXT_PROVIDER: str = "glm"

    # 调用治理：最坏等待 ≈ 超时 × (重试次数 + 1)，超限立即走 mock 兜底，不允许长时间卡住
    LLM_TIMEOUT: int = 60             # 单次调用超时（秒）
    LLM_MAX_RETRIES: int = 0          # JSON 解析失败后的重试次数（0=失败即兜底，先保响应速度）
    LLM_MAX_TOKENS: int = 2048        # 通用/整份方案最大输出 tokens
    LLM_DAY_MAX_TOKENS: int = 700     # 逐天生成方案时单天上限（约 400 字 JSON，够用且防截断）
    LLM_CHAT_MAX_TOKENS: int = 128    # 聊天回复最大输出（提示词要求 ≤80 字）
    LLM_VISION_MAX_TOKENS: int = 1500 # 食物识别输出上限（思考 token 与 JSON 共用预算，留足余量）
    LLM_VISION_THINKING: bool = False # glm-4v-flash 不支持思考模式，关闭避免报错
    LLM_VISION_MAX_RETRIES: int = 2   # 识别 JSON 校验失败重试次数（比文字宽松，防误降级 mock）
    LLM_VISION_RATE_RETRIES: int = 3  # 免费模型 429 限流时的退避重试次数（失败很快，重试成本低）
    LLM_FALLBACK_TO_MOCK: bool = True # 真模型失败后自动降级 mock（演示保命开关）

    # ---- 派生：当前生效的文字 / 视觉配置（业务代码只认这几个）----
    @property
    def is_local(self) -> bool:
        return self.LLM_MODE.strip().lower() == "local"

    @property
    def text_base_url(self) -> str:
        if self.TEXT_PROVIDER.strip().lower() == "qwen":
            return self.QWEN_BASE_URL
        if self.TEXT_PROVIDER.strip().lower() == "local":
            return self.LOCAL_TEXT_BASE_URL
        return self.CLOUD_BASE_URL

    @property
    def text_api_key(self) -> str:
        if self.TEXT_PROVIDER.strip().lower() == "qwen":
            return self.QWEN_API_KEY
        if self.TEXT_PROVIDER.strip().lower() == "local":
            return self.LOCAL_TEXT_API_KEY
        return self.CLOUD_API_KEY

    @property
    def text_model(self) -> str:
        if self.TEXT_PROVIDER.strip().lower() == "qwen":
            return self.QWEN_TEXT_MODEL
        if self.TEXT_PROVIDER.strip().lower() == "local":
            return self.LOCAL_TEXT_MODEL
        return self.CLOUD_TEXT_MODEL

    # 视觉：暂时始终走云端
    @property
    def vision_base_url(self) -> str:
        return self.CLOUD_BASE_URL

    @property
    def vision_api_key(self) -> str:
        return self.CLOUD_API_KEY

    @property
    def vision_model(self) -> str:
        return self.CLOUD_VISION_MODEL

    # pydantic-settings 配置：从 backend/.env 读取
    model_config = {
        "env_file": str(BASE_DIR / ".env"),
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


settings = Settings()

# 上传目录不存在则自动创建
settings.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
