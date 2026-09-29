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


def get_llm_provider() -> LLMProvider:
    if settings.text_api_key:
        real = OpenAICompatProvider()
        if settings.LLM_FALLBACK_TO_MOCK:
            return FallbackProvider(real)
        return real
    return MockProvider()
