"""
Provider 工厂：业务代码统一从这里拿 Provider，永远不直接 new 具体实现

策略：
- 当前模式没有可用 key（如 cloud 模式未填 CLOUD_API_KEY）→ MockProvider，零成本
- 有可用 key → 真模型包一层 FallbackProvider：
  真模型失败（超时/欠费/输出不合法）自动降级 mock，比赛演示永不中断
（模式由 settings.LLM_MODE 决定：cloud=云端，local=本地 Ollama 文字）
"""
import logging

from app.core.config import settings
from app.services.llm.base import LLMError, LLMProvider
from app.services.llm.mock_provider import MockProvider
from app.services.llm.openai_compat import OpenAICompatProvider

logger = logging.getLogger("llm")


class FallbackProvider(LLMProvider):
    """真模型 + mock 兜底的组合"""

    def __init__(self, real: LLMProvider):
        self.real = real
        self.mock = MockProvider()
        self.name = real.name

    def recognize_food(self, image_bytes: bytes, content_type: str, profile_ctx: dict | None = None):
        try:
            return self.real.recognize_food(image_bytes, content_type, profile_ctx)
        except (LLMError, Exception) as e:
            logger.warning("真模型识别失败，降级 mock：%s", e)
            return self.mock.recognize_food(image_bytes, content_type, profile_ctx)

    def generate_plan(self, profile_ctx: dict, days: int, note: str = ""):
        try:
            return self.real.generate_plan(profile_ctx, days, note)
        except (LLMError, Exception) as e:
            logger.warning("真模型方案生成失败，降级 mock：%s", e)
            return self.mock.generate_plan(profile_ctx, days, note)

    def generate_day(self, profile_ctx: dict, day_index: int, days: int,
                     prior_days: list[dict] | None = None, note: str = ""):
        try:
            return self.real.generate_day(profile_ctx, day_index, days, prior_days, note)
        except (LLMError, Exception) as e:
            logger.warning("真模型逐天生成失败，降级 mock：%s", e)
            return self.mock.generate_day(profile_ctx, day_index, days, prior_days, note)

    def chat(self, messages: list[dict]) -> str:
        try:
            return self.real.chat(messages)
        except (LLMError, Exception) as e:
            logger.warning("真模型对话失败，降级 mock：%s", e)
            return self.mock.chat(messages)

    def chat_stream(self, messages: list[dict]):
        try:
            yield from self.real.chat_stream(messages)
        except (LLMError, Exception) as e:
            logger.warning("真模型流式对话失败，降级 mock：%s", e)
            yield from self.mock.chat_stream(messages)

    def revise_plan(self, profile_ctx: dict, current_plan: dict, instruction: str,
                    day_index: int | None = None):
        try:
            return self.real.revise_plan(profile_ctx, current_plan, instruction, day_index)
        except (LLMError, Exception) as e:
            logger.warning("真模型方案微调失败，降级 mock：%s", e)
            return self.mock.revise_plan(profile_ctx, current_plan, instruction, day_index)


def get_llm_provider() -> LLMProvider:
    if settings.text_api_key:
        real = OpenAICompatProvider()
        if settings.LLM_FALLBACK_TO_MOCK:
            return FallbackProvider(real)
        return real
    return MockProvider()
