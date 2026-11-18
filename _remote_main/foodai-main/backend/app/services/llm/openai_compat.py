"""
OpenAICompatProvider：通过 openai 兼容协议调用真模型
- 文字 / 视觉各一个 client：文字可走云端或本地(Ollama)，视觉暂时走云端
- 换厂商或切模式只改 .env，业务代码零改动
"""
import base64
import logging
import time

from openai import OpenAI, OpenAIError, RateLimitError
from pydantic import ValidationError

from app.core.config import settings
from app.services.llm.base import LLMProvider, LLMError, extract_json
from app.services.llm.prompts import (
    build_day_messages, build_food_recognition_prompt, build_plan_messages,
)
from app.services.llm.schemas import PlanDay, PlanResult, VisionRecognitionResult

logger = logging.getLogger("llm")


class OpenAICompatProvider(LLMProvider):
    name = "openai_compat"

    def __init__(self):
        # 文字与视觉分开两个 client（local 模式：文字走 Ollama，视觉仍走云端）
        # max_retries=0：关掉 SDK 自带的隐藏重试，避免一次失败被放大成多次长等待
        self.text_client = OpenAI(
            api_key=settings.text_api_key or "none",
            base_url=settings.text_base_url,
            timeout=settings.LLM_TIMEOUT,
            max_retries=0,
        )
        self.vision_client = OpenAI(
            api_key=settings.vision_api_key or "none",
            base_url=settings.vision_base_url,
            timeout=settings.LLM_TIMEOUT,
            max_retries=0,
        )
        # 本地文字模型（Ollama）连不上时，自动切云端同一份 key（省得答辩现场翻车）
        self.text_fallback_client = None
        self.text_fallback_model = ""
        if (
            settings.TEXT_PROVIDER.strip().lower() == "local"
            and settings.TEXT_FALLBACK_TO_CLOUD
            and settings.CLOUD_API_KEY
        ):
            self.text_fallback_client = OpenAI(
                api_key=settings.CLOUD_API_KEY,
                base_url=settings.CLOUD_BASE_URL,
                timeout=settings.LLM_TIMEOUT,
                max_retries=0,
            )
            self.text_fallback_model = settings.CLOUD_TEXT_MODEL

    # ---------------- 内部：真正发起调用的两个小函数 ----------------

    def _chat(self, messages: list[dict], json_mode: bool = False, max_tokens: int | None = None) -> str:
        """文本调用；json_mode=True 时强制 JSON 输出并降低温度（结构更稳、少触发校验重试）"""
        kwargs = {
            "model": settings.text_model,
            "messages": messages,
            "temperature": 0.3 if json_mode else 0.8,
            "max_tokens": max_tokens or settings.LLM_MAX_TOKENS,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        t0 = time.perf_counter()
        try:
            resp = self.text_client.chat.completions.create(**kwargs)
        except Exception as exc:  # noqa: BLE001 —— 本地 Ollama 没起/崩了，切云端
            if not self.text_fallback_client:
                raise
            logger.warning("本地文字模型不可用（%s），切云端 %s", type(exc).__name__, self.text_fallback_model)
            kwargs["model"] = self.text_fallback_model
            resp = self.text_fallback_client.chat.completions.create(**kwargs)
        logger.info("文本模型=%s 耗时=%.1fs 输出tokens=%s", kwargs["model"],
                    time.perf_counter() - t0, getattr(resp.usage, "completion_tokens", "?"))
        return resp.choices[0].message.content or ""

    def _vision_once(self, messages: list[dict], model: str, thinking: bool, max_tokens: int) -> str:
        """单次视觉调用；thinking 参数走 extra_body 透传（智谱专有）"""
        # glm-4v-flash 的 max_tokens 硬上限是 1024，超出直接 400；思考模型则需要更多空间
        if "glm-4v-flash" in model:
            max_tokens = min(max_tokens, 1024)
        kwargs = {
            "model": model,
            "messages": messages,
            "temperature": 0.3,
            "max_tokens": max_tokens,
        }
        # 思考模型默认会输出大段推理，容易把 JSON 挤到截断；识别场景显式关闭思考
        if "thinking" in model:
            kwargs["extra_body"] = {"thinking": {"type": "enabled" if thinking else "disabled"}}
        elif thinking:
            kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
        t0 = time.perf_counter()
        resp = self.vision_client.chat.completions.create(**kwargs)
        logger.info("视觉模型=%s 耗时=%.1fs 输出tokens=%s", model,
                    time.perf_counter() - t0, getattr(resp.usage, "completion_tokens", "?"))
        return resp.choices[0].message.content or ""

    def _vision(self, messages: list[dict]) -> str:
        """视觉调用（食物识别）：免费模型高峰期限流(429)常见且失败快，带退避重试

        主模型持续限流时，若配置了备用模型则切备用（不带 thinking、限制 token 上限），
        否则抛错由 FallbackProvider 降级 mock。
        """
        attempts = max(1, settings.LLM_VISION_RATE_RETRIES + 1)
        last_err: Exception | None = None
        for i in range(attempts):
            try:
                return self._vision_once(messages, settings.vision_model,
                                         settings.LLM_VISION_THINKING,
                                         settings.LLM_VISION_MAX_TOKENS)
            except RateLimitError as e:
                last_err = e
                if i < attempts - 1:
                    time.sleep(1.2 * (i + 1))
                    logger.warning("视觉模型限流(429)，第 %d 次重试…", i + 1)
        if settings.CLOUD_VISION_FALLBACK_MODEL:
            logger.warning("主视觉模型持续限流，切换备用模型 %s", settings.CLOUD_VISION_FALLBACK_MODEL)
            return self._vision_once(messages, settings.CLOUD_VISION_FALLBACK_MODEL,
                                     False, min(settings.LLM_VISION_MAX_TOKENS, 800))
        raise last_err  # type: ignore[misc]  # 到这里 last_err 必非空

    # ---------------- 接口实现 ----------------

    def recognize_food(self, image_bytes: bytes, content_type: str, profile_ctx: dict | None = None):
        # 图片转 base64 data-url（openai 兼容协议的图片传法，智谱同样支持）
        b64 = base64.b64encode(image_bytes).decode("ascii")
        data_url = f"data:{content_type or 'image/jpeg'};base64,{b64}"

        def build():
            return [{
                "role": "user",
                "content": [
                    {"type": "text", "text": build_food_recognition_prompt()},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }]

        # _call_with_retry 负责：抠 JSON → 校验 → 失败带错误重试（识别重试次数单独配置，防误降级 mock）
        return self._call_with_retry(
            self._vision, build, VisionRecognitionResult,
            max_retries=settings.LLM_VISION_MAX_RETRIES,
        )

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
            lambda m: self._chat(m, json_mode=True, max_tokens=settings.LLM_DAY_MAX_TOKENS),
            build, PlanDay)

    def generate_day_stream(self, profile_ctx: dict, day_index: int, days: int,
                            prior_days: list[dict] | None = None, note: str = ""):
        """逐天流式生成：边收边 yield 进度片段，最后 yield 校验通过的一整天。

        yield 形如：
          {"type": "delta", "text": "..."}            # 模型正在写的原始片段（仅用于显示进度）
          {"type": "day",   "day": PlanDay(...)}      # 最终结构化结果
        全部重试仍失败时抛 LLMError（是否降级 mock 由上层 FallbackProvider 决定）。
        """
        messages = build_day_messages(profile_ctx, day_index, days, prior_days, note)
        last_err = ""
        for _ in range(settings.LLM_MAX_RETRIES + 1):
            buf: list[str] = []
            t0 = time.perf_counter()
            try:
                stream = self.text_client.chat.completions.create(
                    model=settings.text_model, messages=messages,
                    temperature=0.3, max_tokens=settings.LLM_DAY_MAX_TOKENS,
                    response_format={"type": "json_object"}, stream=True,
                )
                for chunk in stream:
                    piece = ""
                    if chunk.choices and chunk.choices[0].delta:
                        piece = chunk.choices[0].delta.content or ""
                    if piece:
                        buf.append(piece)
                        yield {"type": "delta", "text": piece}
            except OpenAIError as e:
                raise LLMError(f"模型调用失败：{e}") from e
            content = "".join(buf)
            logger.info("逐天生成 第%d天 模型=%s 耗时=%.1fs 输出字符=%d",
                        day_index, settings.text_model, time.perf_counter() - t0, len(content))
            try:
                day = PlanDay.model_validate(extract_json(content))
                yield {"type": "day", "day": day}
                return
            except (LLMError, ValidationError) as e:
                last_err = str(e)[:300]
                messages = messages + [{
                    "role": "user",
                    "content": (
                        f"你上次的输出无法通过校验：{last_err}。"
                        "请严格只输出一个合法的 JSON 对象，不要任何解释文字或 markdown 代码块标记。"
                    ),
                }]
        raise LLMError(f"模型输出 JSON 校验失败（已重试）：{last_err}")

    def chat(self, messages: list[dict], max_tokens: int | None = None) -> str:
        # 纯文字沟通：不强制 JSON，限制长度让它更短更快
        return self._chat(messages, max_tokens=max_tokens or settings.LLM_CHAT_MAX_TOKENS)

    def chat_stream(self, messages: list[dict]):
        # 流式对话：逐块产出文本，前端可边收边显示；日志记录首字与总时长
        t0 = time.perf_counter()
        ttft: float | None = None
        stream = self.text_client.chat.completions.create(
            model=settings.text_model, messages=messages,
            temperature=0.8, max_tokens=settings.LLM_CHAT_MAX_TOKENS, stream=True,
        )
        for chunk in stream:
            if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                if ttft is None:
                    ttft = time.perf_counter() - t0
                yield chunk.choices[0].delta.content
        logger.info("流式聊天 模型=%s 首字=%.1fs 总耗时=%.1fs", settings.text_model,
                    ttft if ttft is not None else -1, time.perf_counter() - t0)
