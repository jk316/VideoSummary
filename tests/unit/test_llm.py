"""LLM 模块单元测试：mock AsyncOpenAI 测 generate/acheck/aclose/错误映射/工厂模式。"""

from dataclasses import dataclass
from pathlib import Path

import pytest

from app.config.schema import LlmConfig, NetworkConfig
from app.core.errors import LlmError
from app.core.models import TokenUsage
from app.llm.base import LLMResponse, Message
from app.llm.openai_compat import (
    OpenAICompatClient,
    _extract_status_code,
    _extract_usage,
    _map_openai_error,
    make_openai_factory,
)


def _make_config(**overrides: object) -> LlmConfig:
    kwargs = {
        "base_url": "https://api.openai.com/v1",
        "api_key": "sk-test",
        "model": "gpt-4o-mini",
        "timeout_seconds": 30,
        "max_retries": 1,
        "max_concurrency": 1,
    }
    kwargs.update(overrides)  # type: ignore[typeddict-item]
    return LlmConfig(**kwargs)  # type: ignore[arg-type]


def _make_network(**overrides: object) -> NetworkConfig:
    kwargs = {"proxy": "", "use_system_proxy": False}
    kwargs.update(overrides)  # type: ignore[typeddict-item]
    return NetworkConfig(**kwargs)  # type: ignore[arg-type]


@dataclass
class _FakeChoice:
    message: object

    @dataclass
    class _Msg:
        content: str | None = "response text"

    def __init__(self, content: str = "response text") -> None:
        self.message = self._Msg(content)


@dataclass
class _FakeUsage:
    prompt_tokens: int = 10
    completion_tokens: int = 5


@dataclass
class _FakeCompletion:
    choices: list[_FakeChoice]
    usage: _FakeUsage | None = None


@dataclass
class _FakeModel:
    id: str


@dataclass
class _FakeModelsPage:
    data: list[_FakeModel]


class _FakeAsyncChat:
    def __init__(self) -> None:
        self.completions = _FakeAsyncCompletions()


class _FakeAsyncCompletions:
    """由 monkeypatch 设置 create 行为。"""

    pass


class _FakeAsyncModels:
    """由 monkeypatch 设置 list 行为。"""

    pass


class _FakeAsyncClient:
    def __init__(self) -> None:
        self.chat = _FakeAsyncChat()
        self.models = _FakeAsyncModels()

    async def close(self) -> None:
        pass


def _make_client(
    config: LlmConfig | None = None,
    network: NetworkConfig | None = None,
    tmp_path: Path | None = None,
) -> OpenAICompatClient:
    return OpenAICompatClient(
        config=config or _make_config(),
        network=network or _make_network(),
        models_dir=tmp_path or Path("."),
    )


class TestFactory:
    def test_factory_returns_callable(self) -> None:
        factory = make_openai_factory(_make_config(), _make_network(), Path("."))
        assert callable(factory)
        client = factory()
        assert isinstance(client, OpenAICompatClient)

    def test_factory_creates_new_instance_each_time(self) -> None:
        factory = make_openai_factory(_make_config(), _make_network(), Path("."))
        c1 = factory()
        c2 = factory()
        assert c1 is not c2


class TestGenerate:
    async def test_returns_text_and_usage(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        client = _make_client(tmp_path=tmp_path)

        async def fake_create(self, *, model, messages, timeout=None, max_retries=None):
            return _FakeCompletion(
                choices=[_FakeChoice("hello world")],
                usage=_FakeUsage(prompt_tokens=100, completion_tokens=50),
            )

        async def fake_list(self):
            return _FakeModelsPage(data=[_FakeModel("gpt-4o-mini")])

        monkeypatch.setattr(
            "openai.resources.chat.completions.AsyncCompletions.create", fake_create
        )
        monkeypatch.setattr("openai.resources.models.AsyncModels.list", fake_list)

        result = await client.generate([Message(role="user", content="hi")])
        assert result == LLMResponse(
            text="hello world", usage=TokenUsage(prompt_tokens=100, completion_tokens=50)
        )

    async def test_system_and_user_messages_passed(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        client = _make_client(tmp_path=tmp_path)
        msgs_passed: list = []

        async def fake_create(self, *, model, messages, timeout=None, max_retries=None):
            msgs_passed.extend(messages)
            return _FakeCompletion(choices=[_FakeChoice("ok")], usage=_FakeUsage(0, 0))

        monkeypatch.setattr(
            "openai.resources.chat.completions.AsyncCompletions.create", fake_create
        )

        await client.generate(
            [Message(role="system", content="sys"), Message(role="user", content="q")]
        )
        assert msgs_passed == [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "q"},
        ]

    async def test_empty_content_defaults_to_empty_string(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        client = _make_client(tmp_path=tmp_path)

        async def fake_create(self, **kw):
            return _FakeCompletion(choices=[_FakeChoice(None)])

        monkeypatch.setattr(
            "openai.resources.chat.completions.AsyncCompletions.create", fake_create
        )

        result = await client.generate([Message(role="user", content="x")])
        assert result.text == ""


class TestAcheck:
    async def test_model_found_passes(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        client = _make_client(tmp_path=tmp_path)

        async def fake_list(self):
            return _FakeModelsPage(data=[_FakeModel("gpt-4o-mini")])

        monkeypatch.setattr("openai.resources.models.AsyncModels.list", fake_list)
        await client.acheck()  # 不应抛出

    async def test_model_not_found_warns(
        self, monkeypatch: pytest.MonkeyPatch, caplog, tmp_path: Path
    ) -> None:
        client = _make_client(tmp_path=tmp_path)

        async def fake_list(self):
            return _FakeModelsPage(data=[_FakeModel("other-model")])

        monkeypatch.setattr("openai.resources.models.AsyncModels.list", fake_list)
        await client.acheck()  # 不抛异常，仅告警
        assert any("gpt-4o-mini" in r.message for r in caplog.records)


class TestAclose:
    async def test_releases_client(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        client = _make_client(tmp_path=tmp_path)

        # 先初始化 _client
        async def fake_list(self):
            return _FakeModelsPage(data=[_FakeModel("gpt-4o-mini")])

        monkeypatch.setattr("openai.resources.models.AsyncModels.list", fake_list)
        await client.acheck()
        assert client._client is not None

        await client.aclose()
        assert client._client is None

    async def test_idempotent_on_uninitialized_client(self, tmp_path: Path) -> None:
        client = _make_client(tmp_path=tmp_path)
        await client.aclose()  # 未初始化也不应抛出
        assert client._client is None


class TestErrorMapping:
    def test_status_code_takes_priority(self) -> None:
        class Exc(Exception):
            status_code = 401

        err = _map_openai_error(Exc(), "gpt-4")
        assert "Key" in err.user_message

    def test_403_status_code(self) -> None:
        class Exc(Exception):
            status_code = 403

        err = _map_openai_error(Exc(), "gpt-4")
        assert "配额" in err.user_message

    def test_500_status_code(self) -> None:
        class Exc(Exception):
            status_code = 500

        err = _map_openai_error(Exc(), "gpt-4")
        assert "暂时不可用" in err.user_message

    def test_401_fallback_substring(self) -> None:
        err = _map_openai_error(ValueError("Error code: 401 - Invalid API Key"), "gpt-4")
        assert "Key" in err.user_message

    def test_timeout_is_network_error(self) -> None:
        err = _map_openai_error(TimeoutError("Connection timed out"), "gpt-4")
        assert "网络" in err.user_message or "连接" in err.user_message

    def test_unknown_falls_back_to_generic_message(self) -> None:
        err = _map_openai_error(ValueError("something weird"), "gpt-4")
        assert err.user_message != LlmError.default_user_message
        assert "详情见日志" in err.user_message


class TestExtractStatusCode:
    def test_direct_status_code(self) -> None:
        class Exc(Exception):
            status_code = 401

        assert _extract_status_code(Exc()) == 401

    def test_nested_response(self) -> None:
        class Resp:
            status_code = 503

        class Exc(Exception):
            response = Resp()

        assert _extract_status_code(Exc()) == 503

    def test_none_when_absent(self) -> None:
        assert _extract_status_code(ValueError("x")) is None


class TestExtractUsage:
    def test_none_usage_returns_zero(self) -> None:
        completion = _FakeCompletion(choices=[_FakeChoice()], usage=None)
        assert _extract_usage(completion) == TokenUsage()

    def test_valid_usage_extracted(self) -> None:
        completion = _FakeCompletion(choices=[_FakeChoice()], usage=_FakeUsage(20, 10))
        assert _extract_usage(completion) == TokenUsage(prompt_tokens=20, completion_tokens=10)


class TestFullRoundtrip:
    async def test_generate_then_aclose_lifecycle(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        client = _make_client(tmp_path=tmp_path)
        call_count = 0

        async def fake_create(self, **kw):
            nonlocal call_count
            call_count += 1
            return _FakeCompletion(choices=[_FakeChoice("first")], usage=_FakeUsage(1, 1))

        monkeypatch.setattr(
            "openai.resources.chat.completions.AsyncCompletions.create", fake_create
        )

        r1 = await client.generate([Message(role="user", content="q1")])
        assert r1.text == "first"
        r2 = await client.generate([Message(role="user", content="q2")])
        assert r2.text == "first"
        assert call_count == 2  # 同一客户端多次调用复用连接

        await client.aclose()


class TestProxyInjection:
    def test_proxy_resolve_called_with_config_proxy(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        captured: list[tuple] = []

        def fake_resolve(proxy: str, use_system: bool) -> str | None:
            captured.append((proxy, use_system))
            return None  # 不实际走 httpx，仅验证参数传入

        monkeypatch.setattr("app.llm.openai_compat.resolve_httpx_proxy", fake_resolve)
        network = _make_network(proxy="http://127.0.0.1:7890")
        client = _make_client(network=network, tmp_path=tmp_path)
        client._build_http_client()
        assert captured == [("http://127.0.0.1:7890", False)]
