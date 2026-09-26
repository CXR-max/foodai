"""
LLMProvider 抽象基类 + JSON 解析重试框架

所有大模型实现（Mock / OpenAI 兼容）都要实现两个方法：
- recognize_food()：识别食物图片
- generate_plan()：生成养生方案

_call_with_retry() 是公共的"结构化输出保险"：
模型输出 → 抠 JSON → Pydantic 校验 → 失败带错误信息重试 → 耗尽抛 LLMError
（上层 provider.py 的 FallbackProvider 捕获 LLMError 后降级 mock）
"""
import json
from abc import ABC, abstractmethod

from pydantic import BaseModel, ValidationError

from app.core.config import settings


class LLMError(Exception):
    """LLM 调用/输出解析失败的统一异常"""


def extract_json(text: str) -> dict:
    """从模型输出文本里抠出 JSON 对象（容忍 ```json 围栏和前后多余文字）"""
    if not text:
        raise LLMError("模型返回了空内容")
    text = text.strip()
    # 去掉 markdown 代码块围栏
    if "```" in text:
        parts = text.split("```")
        # 取第一个看起来像 JSON 的片段
        for part in parts:
            part = part.strip()
            if part.startswith("json"):
                part = part[4:].strip()
            if part.startswith("{"):
                text = part
                break
    # 截取首个 { 到末个 } 之间的内容
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise LLMError(f"输出中找不到 JSON 对象：{text[:100]}")
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError as e:
        raise LLMError(f"JSON 解析失败：{e}") from e


class LLMProvider(ABC):
    """大模型 Provider 统一接口"""

    name: str = "base"  # 实现类要标明自己的名字（落库到 recognitions.provider）

    @abstractmethod
    def recognize_food(
        self, image_bytes: bytes, content_type: str, profile_ctx: dict | None = None
    ):
        """识别食物图片，返回 VisionRecognitionResult（不含营养值，营养由营养库算）"""

    @abstractmethod
    def generate_plan(self, profile_ctx: dict, days: int, note: str = ""):
        """生成养生方案，返回 PlanResult"""

    @abstractmethod
    def generate_day(self, profile_ctx: dict, day_index: int, days: int,
                     prior_days: list[dict] | None = None, note: str = ""):
        """只生成方案中的某一天（用于逐天流式生成），返回 PlanDay"""

    @abstractmethod
    def chat(self, messages: list[dict]) -> str:
        """生成方案前的纯文字对话，返回模型回复文本（messages 已含 system）"""

    @abstractmethod
    def chat_stream(self, messages: list[dict]):
        """流式对话：逐块 yield 文本片段（生成器）"""

    @abstractmethod
    def revise_plan(self, profile_ctx: dict, current_plan: dict, instruction: str,
                    day_index: int | None = None):
        """基于当前方案 JSON 做最小改动，返回修订后的 PlanResult"""

    # ---- 公共：结构化输出 + 校验重试 ----

    def _call_with_retry(
        self,
        call_fn,            # call_fn(messages) -> str：真正发起模型调用的函数
        build_messages,     # build_messages() -> list[dict]：构造初始消息
        model_cls: type[BaseModel],
    ):
        """带重试的 JSON 结构化调用"""
        messages = build_messages()
        last_err = ""
        for _ in range(settings.LLM_MAX_RETRIES + 1):
            content = call_fn(messages)
            data = extract_json(content)
            try:
                return model_cls.model_validate(data)
            except ValidationError as e:
                # 把校验错误喂回给模型，要求重新输出
                last_err = str(e)[:300]
                messages = messages + [{
                    "role": "user",
                    "content": (
                        f"你上次的输出无法通过校验：{last_err}。"
                        "请严格只输出一个合法的 JSON 对象，不要任何解释文字或 markdown 代码块标记。"
                    ),
                }]
        raise LLMError(f"模型输出 JSON 校验失败（已重试）：{last_err}")
