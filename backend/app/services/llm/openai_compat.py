"""
OpenAICompatProvider：通过 openai 兼容协议调用真模型
- 文字 / 视觉各一个 client：文字可走云端或本地(Ollama)，视觉暂时走云端
- 换厂商或切模式只改 .env，业务代码零改动
"""
import base64
import logging

from openai import OpenAI

from app.core.config import settings
from app.services.llm.base import LLMProvider, LLMError
from app.services.llm.prompts import (
    FOOD_RECOGNITION_PROMPT, build_day_messages, build_plan_messages, build_revise_messages,
)
from app.services.llm.schemas import PlanDay, PlanResult, VisionRecognitionResult

logger = logging.getLogger("llm")


class OpenAICompatProvider(LLMProvider):
    name = "openai_compat"

    def __init__(self):
        # 文字与视觉分开两个 client（local 模式：文字走 Ollama，视觉仍走云端）
        self.text_client = OpenAI(
            api_key=settings.text_api_key or "none",
            base_url=settings.text_base_url,
            timeout=settings.LLM_TIMEOUT,
        )
        self.vision_client = OpenAI(
            api_key=settings.vision_api_key or "none",
            base_url=settings.vision_base_url,
            timeout=settings.LLM_TIMEOUT,
        )

    # ---------------- 内部：真正发起调用的两个小函数 ----------------

    def _chat(self, messages: list[dict], json_mode: bool = False, max_tokens: int | None = None) -> str:
        """文本调用；json_mode=True 时强制 JSON 输出并限制长度（防截断/防重试放大）"""
        kwargs = {
            "model": settings.text_model,
            "messages": messages,
            "temperature": 0.8,
            "max_tokens": max_tokens or settings.LLM_MAX_TOKENS,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        resp = self.text_client.chat.completions.create(**kwargs)
        logger.info("文本模型=%s 输出tokens=%s", settings.text_model,
                    getattr(resp.usage, "completion_tokens", "?"))
        return resp.choices[0].message.content or ""

    def _vision(self, messages: list[dict]) -> str:
        """视觉调用（食物识别）"""
        resp = self.vision_client.chat.completions.create(
            model=settings.vision_model, messages=messages, temperature=0.3,
        )
        return resp.choices[0].message.content or ""

    # ---------------- 接口实现 ----------------

    def recognize_food(self, image_bytes: bytes, content_type: str, profile_ctx: dict | None = None):
        # 图片转 base64 data-url（openai 兼容协议的图片传法，智谱同样支持）
        b64 = base64.b64encode(image_bytes).decode("ascii")
        data_url = f"data:{content_type or 'image/jpeg'};base64,{b64}"

        def build():
            return [{
                "role": "user",
                "content": [
                    {"type": "text", "text": FOOD_RECOGNITION_PROMPT},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }]

        # _call_with_retry 负责：抠 JSON → 校验 → 失败带错误重试
        return self._call_with_retry(self._vision, build, VisionRecognitionResult)

    def generate_plan(self, profile_ctx: dict, days: int, note: str = ""):
        def build():
            return build_plan_messages(profile_ctx, days, note)

        result = self._call_with_retry(
            lambda m: self._chat(m, json_mode=True), build, PlanResult)
        # 天数不够时截断/报错保护
        if len(result.days) > days:
            result.days = result.days[:days]
        return result

    def generate_day(self, profile_ctx: dict, day_index: int, days: int,
                     prior_days: list[dict] | None = None, note: str = ""):
        def build():
            return build_day_messages(profile_ctx, day_index, days, prior_days, note)

        return self._call_with_retry(
            lambda m: self._chat(m, json_mode=True), build, PlanDay)

    def chat(self, messages: list[dict]) -> str:
        # 纯文字沟通：不强制 JSON，限制长度让它更短更快
        return self._chat(messages, max_tokens=settings.LLM_CHAT_MAX_TOKENS)

    def chat_stream(self, messages: list[dict]):
        # 流式对话：逐块产出文本，前端可边收边显示
        stream = self.text_client.chat.completions.create(
            model=settings.text_model, messages=messages,
            temperature=0.8, max_tokens=settings.LLM_CHAT_MAX_TOKENS, stream=True,
        )
        for chunk in stream:
            if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

    def revise_plan(self, profile_ctx: dict, current_plan: dict, instruction: str,
                    day_index: int | None = None):
        def build():
            return build_revise_messages(current_plan, instruction, day_index)

        return self._call_with_retry(
            lambda m: self._chat(m, json_mode=True), build, PlanResult)
