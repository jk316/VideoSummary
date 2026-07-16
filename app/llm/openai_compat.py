"""OpenAI 兼容 LLM 客户端：AsyncOpenAI + 工厂模式 + httpx 代理注入。

``LLMClientFactory`` 解决 ``asyncio.run()`` 每次创建/销毁事件循环时
AsyncOpenAI 连接池绑定的生命周期问题（architecture.md §5.6 / §7）。
"""

import logging
from pathlib import Path

from app.config.schema import LlmConfig, NetworkConfig
from app.core.errors import LlmError
from app.core.models import TokenUsage
from app.llm.base import LLMClient, LLMClientFactory, LLMResponse, Message
from app.utils.proxy import resolve_httpx_proxy

logger = logging.getLogger(__name__)


def make_openai_factory(
    config: LlmConfig, network: NetworkConfig, models_dir: Path
) -> LLMClientFactory:
    """创建 OpenAI 兼容客户端工厂。

    工厂每次调用创建新 SDK 实例——与其生命周期绑定的 httpx 异步连接池
    在该次 asyncio 事件循环中使用、finally 中 aclose() 销毁。
    """

    def factory() -> LLMClient:
        return OpenAICompatClient(config=config, network=network, models_dir=models_dir)

    return factory


class OpenAICompatClient(LLMClient):
    """OpenAI 兼容协议客户端（base_url 可配，覆盖 DeepSeek/Qwen/本地 LLM 等）。"""

    def __init__(self, config: LlmConfig, network: NetworkConfig, models_dir: Path) -> None:
        self._config = config
        self._network = network
        self._models_dir = models_dir
        self._client: object | None = None

    async def generate(self, messages: list[Message]) -> LLMResponse:
        client = self._ensure_client()
        try:
            completion = await client.chat.completions.create(  # type: ignore[union-attr]
                model=self._config.model,
                messages=[{"role": m.role, "content": m.content} for m in messages],
                timeout=self._config.timeout_seconds,
                max_retries=self._config.max_retries,
            )
        except Exception as exc:
            raise _map_openai_error(exc, self._config.model) from exc
        choice = completion.choices[0]
        text = choice.message.content or ""
        usage = _extract_usage(completion)
        return LLMResponse(text=text, usage=usage)

    async def acheck(self) -> None:
        """列出模型并验证目标模型存在——网络/凭证问题即刻暴露。"""
        client = self._ensure_client()
        try:
            models_page = await client.models.list()  # type: ignore[union-attr]
        except Exception as exc:
            raise _map_openai_error(exc, self._config.model) from exc
        model = self._config.model.rstrip("/").split("/")[-1]
        found = any(m.id == model for m in models_page.data)
        if not found:
            logger.warning("模型 %s 不在 API 返回的模型列表中，将继续尝试调用", model)

    async def aclose(self) -> None:
        if self._client is not None and hasattr(self._client, "close"):
            await self._client.close()  # type: ignore[union-attr]
        self._client = None

    # ---------------------------------------------------------------- 内部

    def _ensure_client(self) -> object:
        if self._client is not None:
            return self._client
        from openai import AsyncOpenAI

        http_client = self._build_http_client()
        self._client = AsyncOpenAI(
            base_url=self._config.base_url.rstrip("/"),
            api_key=self._config.api_key,
            http_client=http_client,
            max_retries=0,  # SDK 内置重试关闭，重试用 httpx 自带 + 外层指数退避
        )
        return self._client

    def _build_http_client(self) -> object:
        import httpx

        proxy = resolve_httpx_proxy(self._network.proxy, self._network.use_system_proxy)
        return httpx.AsyncClient(
            proxy=proxy,
            timeout=httpx.Timeout(self._config.timeout_seconds),
            transport=httpx.AsyncHTTPTransport(retries=self._config.max_retries),
        )


# -------------------------------------------------------------------- 纯函数


def _extract_usage(completion: object) -> TokenUsage:
    usage_obj = getattr(completion, "usage", None)
    if usage_obj is None:
        return TokenUsage()
    return TokenUsage(
        prompt_tokens=getattr(usage_obj, "prompt_tokens", 0) or 0,
        completion_tokens=getattr(usage_obj, "completion_tokens", 0) or 0,
    )


def _map_openai_error(exc: Exception, model: str) -> LlmError:
    """优先检查结构化 status_code，回退到异常消息子串匹配。

    OpenAI SDK / httpx 等异常对象上通常带有 status_code 属性；
    非标准 API 或无结构化属性的异常走子串回退路径。
    """
    message = str(exc)
    code = _extract_status_code(exc)

    if code is not None:
        if code == 401:
            return LlmError(
                f"API 鉴权失败 (401): {message}", user_message="API Key 无效，请检查设置。"
            )
        if code in (403, 429):
            return LlmError(
                f"API 限流/配额 ({code}): {message}",
                user_message="API 配额不足或限流，请稍后重试或检查账户。",
            )
        if code == 404:
            return LlmError(
                f"模型不可用 ({model}): {message}",
                user_message=f"模型 {model} 不存在或无访问权限。",
            )
        if 500 <= code < 600:
            return LlmError(
                f"LLM 服务异常 ({code}): {message}",
                user_message="LLM 服务暂时不可用，请稍后重试。",
            )

    # 无 status_code → 回退子串匹配（兼容非标准 API）
    lowered = message.lower()
    if any(k in lowered for k in ("401", "unauthorized", "invalid api key", "incorrect api key")):
        return LlmError(f"API 鉴权失败: {message}", user_message="API Key 无效，请检查设置。")
    if any(k in lowered for k in ("429", "quota", "rate limit", "insufficient_quota")):
        return LlmError(
            f"API 限流/配额: {message}",
            user_message="API 配额不足或限流，请稍后重试或检查账户。",
        )
    if any(k in lowered for k in ("timeout", "connection", "getaddrinfo", "proxy", "refused")):
        return LlmError(
            f"LLM 网络错误: {message}",
            user_message="无法连接 LLM 服务，请检查网络、代理或 base_url。",
        )
    return LlmError(f"LLM 调用失败: {message}", user_message="调用大模型接口失败，详情见日志。")


def _extract_status_code(exc: Exception) -> int | None:
    """从异常对象上提取 HTTP 状态码（覆盖 openai / httpx / requests）。"""
    for attr in ("status_code",):
        val = getattr(exc, attr, None)
        if isinstance(val, int):
            return val
    response = getattr(exc, "response", None)
    if response is not None:
        val = getattr(response, "status_code", None)
        if isinstance(val, int):
            return val
    return None
