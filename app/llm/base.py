"""LLM 客户端抽象接口与工厂类型。"""

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass

from app.core.models import TokenUsage


@dataclass(frozen=True)
class Message:
    role: str  # "system" | "user" | "assistant"
    content: str


@dataclass(frozen=True)
class LLMResponse:
    text: str
    usage: TokenUsage


class LLMClient(ABC):
    """LLM 调用的统一接口。"""

    @abstractmethod
    async def generate(self, messages: list[Message]) -> LLMResponse:
        """发送消息列表，返回文本与 token 用量。"""

    @abstractmethod
    async def acheck(self) -> None:
        """轻量连通性/鉴权校验（任务开始前快速失败，给出明确提示）。

        Raises:
            LlmError: API Key 无效、网络不通或模型不存在。
        """

    @abstractmethod
    async def aclose(self) -> None:
        """关闭底层连接池（httpx AsyncClient）。"""


LLMClientFactory = Callable[[], LLMClient]
"""每次请求创建新客户端——避免 AsyncOpenAI 连接池跨 asyncio.run() 复用。"""
