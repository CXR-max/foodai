"""
Provider 工厂：业务代码统一从这里拿 Provider，永远不直接 new 具体实现

策略：
- 当前模式没有可用 key（如 cloud 模式未填 CLOUD_API_KEY）→ MockProvider，零成本
- 有可用 key → 真模型包一层 FallbackProvider：
  真模型失败（超时/欠费/输出不合法）自动降级 mock，比赛演示永不中断
（模式由 settings.LLM_MODE 决定：cloud=云端，local=本地 Ollama 文字）
"""
import logging

from openai import OpenAIError
from pydantic import ValidationError

from app.core.config import settings
from app.services.llm.base import LLMError, LLMProvider
from app.services.llm.mock_provider import MockProvider
from app.services.llm.openai_compat import OpenAICompatProvider
from app.services.planner import build_day, build_plan

logger = logging.getLogger("llm")

# 只有"调用类"异常才降级 mock（超时/网络/限流/输出不合法）；
# 代码 bug（AttributeError 等）应直接暴露，不拿 mock 掩盖
FALLBACK_ERRORS = (LLMError, OpenAIError, ValidationError)


class FallbackProvider(LLMProvider):
    """真模型 + mock 兜底的组合"""

    def __init__(self, real: LLMProvider):
        self.real = real
        self.mock = MockProvider()
        self.name = real.name
        self._used = real.name   # 记录本次真正产出内容的一方（供落库/前端如实标注）

    @property
    def active_name(self) -> str:
        return self._used

    def recognize_food(self, image_bytes: bytes, content_type: str, profile_ctx: dict | None = None):
        try:
            return self.real.recognize_food(image_bytes, content_type, profile_ctx)
        except FALLBACK_ERRORS as e:
            self._used = "mock"
            logger.warning("真模型识别失败，降级 mock：%s", e)
            return self.mock.recognize_food(image_bytes, content_type, profile_ctx)

    def generate_plan(self, profile_ctx: dict, days: int, note: str = ""):
        try:
            return self.real.generate_plan(profile_ctx, days, note)
        except FALLBACK_ERRORS as e:
            self._used = "mock"
            logger.warning("真模型方案生成失败，降级 mock：%s", e)
            return self.mock.generate_plan(profile_ctx, days, note)

    def generate_day(self, profile_ctx: dict, day_index: int, days: int,
                     prior_days: list[dict] | None = None, note: str = ""):
        try:
            return self.real.generate_day(profile_ctx, day_index, days, prior_days, note)
        except FALLBACK_ERRORS as e:
            self._used = "mock"
            logger.warning("真模型逐天生成失败，降级 mock：%s", e)
            return self.mock.generate_day(profile_ctx, day_index, days, prior_days, note)

    def generate_day_stream(self, profile_ctx: dict, day_index: int, days: int,
                            prior_days: list[dict] | None = None, note: str = ""):
        got_day = False
        try:
            for event in self.real.generate_day_stream(profile_ctx, day_index, days, prior_days, note):
                if event.get("type") == "day":
                    got_day = True
                yield event
        except FALLBACK_ERRORS as e:
            self._used = "mock"
            logger.warning("真模型逐天流式失败，降级 mock：%s", e)
        if not got_day:
            # delta 只是进度显示，没有最终结果才需要兜底
            yield {"type": "day",
                   "day": self.mock.generate_day(profile_ctx, day_index, days, prior_days, note)}

    def chat(self, messages: list[dict]) -> str:
        try:
            return self.real.chat(messages)
        except FALLBACK_ERRORS as e:
            self._used = "mock"
            logger.warning("真模型对话失败，降级 mock：%s", e)
            return self.mock.chat(messages)

    def chat_stream(self, messages: list[dict]):
        started = False
        try:
            for piece in self.real.chat_stream(messages):
                started = True
                yield piece
        except FALLBACK_ERRORS as e:
            if started:
                # 已经吐字给用户了，不能再从头拼一段 mock（会出现半截真答+整段假答）
                raise LLMError(f"流式对话中断：{e}") from e
            self._used = "mock"
            logger.warning("真模型流式对话失败，降级 mock：%s", e)
            yield from self.mock.chat_stream(messages)


class SolverProvider(MockProvider):
    """方案构造走 CP-SAT 约束求解器；识别/对话仍转发给原 Provider。

    - generate_plan / generate_day / generate_day_stream → planner
    - recognize_food / chat / chat_stream → 原 Provider（无 key 时即 MockProvider）
    - 方案求解失败不降级 mock，直接抛 PlannerError（避免拿假方案冒充结果）
    """

    name = "solver"

    def __init__(self, base: LLMProvider):
        self.base = base
        # 保留 /api/llm/status?probe=1 直连探测语义（跳过 FallbackProvider 的 mock 兜底）
        self.real = getattr(base, "real", base)
        self._used = base.active_name
        self._plan_meta: dict = {}

    @property
    def active_name(self) -> str:
        return self._used

    @property
    def plan_note(self) -> str:
        """本次方案是否发生过蛋白放宽，供落库 summary 如实展示。"""
        relaxed = self._plan_meta.get("relaxed", 1.0)
        if relaxed < 1.0:
            factor = self._plan_meta.get("protein_factor")
            if factor:
                return (
                    f"蛋白下限已放宽到 {factor * relaxed:.2f} g/kg"
                    f"（原 {factor:g} g/kg，当前忌口组合下无法满足原目标）"
                )
            return f"蛋白下限已放宽到 {relaxed:.0%}（当前忌口组合下无法满足原目标）"
        return ""

    @property
    def variety_note(self) -> str:
        """窄忌口池下是否放宽了跨天不重复硬约束。"""
        if self._plan_meta.get("variety_relaxed"):
            return "为避免当前忌口组合无解，已放宽跨天不重复要求"
        return ""

    def recognize_food(self, image_bytes: bytes, content_type: str, profile_ctx: dict | None = None):
        result = self.base.recognize_food(image_bytes, content_type, profile_ctx)
        self._used = self.base.active_name   # 兜底到 mock 时如实标注
        return result

    def generate_plan(self, profile_ctx: dict, days: int, note: str = ""):
        self._used = "solver"
        self._plan_meta = {}
        return build_plan(profile_ctx, days, note, meta=self._plan_meta)

    def generate_day(self, profile_ctx: dict, day_index: int, days: int,
                     prior_days: list[dict] | None = None, note: str = ""):
        self._used = "solver"
        if day_index == 1:
            self._plan_meta = {}
        return build_day(profile_ctx, day_index, days, prior_days, note, self._plan_meta)

    def generate_day_stream(self, profile_ctx: dict, day_index: int, days: int,
                            prior_days: list[dict] | None = None, note: str = ""):
        self._used = "solver"
        if day_index == 1:
            self._plan_meta = {}
        yield {"type": "day",
               "day": build_day(profile_ctx, day_index, days, prior_days, note, self._plan_meta)}

    def chat(self, messages: list[dict]) -> str:
        reply = self.base.chat(messages)
        self._used = self.base.active_name
        return reply

    def chat_stream(self, messages: list[dict]):
        self._used = self.base.active_name
        yield from self.base.chat_stream(messages)


def _build_base_provider() -> LLMProvider:
    """原有 Provider 选择逻辑：有 key 用真模型（可选 mock 兜底），无 key 用 MockProvider"""
    if settings.text_api_key:
        real = OpenAICompatProvider()
        if settings.LLM_FALLBACK_TO_MOCK:
            return FallbackProvider(real)
        return real
    return MockProvider()


def get_llm_provider() -> LLMProvider:
    base = _build_base_provider()
    if settings.PLAN_ENGINE.strip().lower() == "solver":
        return SolverProvider(base)
    return base
