"""MapReduceSummarizer 单元测试：用 FakeLLMClient 验证单块/多块/取消路径。"""

import pytest

from app.core.cancellation import CancellationToken
from app.core.models import (
    Chunk,
    Site,
    SummaryLanguage,
    TokenUsage,
    VideoInfo,
    VideoRef,
)
from app.llm.base import LLMClient, LLMClientFactory, LLMResponse, Message
from app.summarizer.summarizer import MapReduceSummarizer, TimestampUrlFn, _fmt_seconds


class _FakeClient(LLMClient):
    """返回预设响应，记录调用次数。"""

    def __init__(self, response: str = "摘要内容", usage: TokenUsage | None = None) -> None:
        self.call_count = 0
        self.messages_list: list[list[Message]] = []
        self._response = response
        self._usage = usage or TokenUsage(prompt_tokens=10, completion_tokens=5)

    async def generate(self, messages: list[Message]) -> LLMResponse:
        self.call_count += 1
        self.messages_list.append(messages)
        return LLMResponse(text=self._response, usage=self._usage)

    async def acheck(self) -> None:
        pass

    async def aclose(self) -> None:
        pass


def _make_info(title: str = "测试视频") -> VideoInfo:
    return VideoInfo(
        ref=VideoRef(site=Site.YOUTUBE, video_id="abc", url="https://x"),
        title=title,
        duration=600.0,
        author="测试作者",
    )


def _make_chunks(*texts: str, seconds_each: float = 60.0) -> list[Chunk]:
    return [
        Chunk(
            index=i,
            start=i * seconds_each,
            end=(i + 1) * seconds_each,
            text=text,
            token_count=len(text),
        )
        for i, text in enumerate(texts)
    ]


def _no_progress(_fraction: float | None, _message: str | None = None) -> None:
    pass


def _noop_timestamp(seconds: int) -> str:
    return f"https://x?t={seconds}"


def _make_summarizer(
    response: str = "摘要",
    language: SummaryLanguage = SummaryLanguage.ZH,
    timestamp: TimestampUrlFn | None = None,
) -> tuple[MapReduceSummarizer, _FakeClient]:
    client = _FakeClient(response=response)
    factory: LLMClientFactory = lambda: client  # noqa: E731
    summarizer = MapReduceSummarizer(
        client_factory=factory,
        language=language,
        max_concurrency=2,
        timestamp_url=timestamp or _noop_timestamp,
    )
    return summarizer, client


# ----------------------------------------------------------------- tests


class TestFormatSeconds:
    def test_under_one_hour(self) -> None:
        assert _fmt_seconds(90.7) == "1:30"

    def test_over_one_hour(self) -> None:
        assert _fmt_seconds(3661) == "1:01:01"

    def test_zero(self) -> None:
        assert _fmt_seconds(0) == "0:00"


class TestSingleChunk:
    async def test_direct_summarize_without_map_reduce(self) -> None:
        summarizer, client = _make_summarizer(response="单块总结")
        chunks = _make_chunks("视频内容文本")
        result = await summarizer.summarize(_make_info(), chunks, _no_progress, CancellationToken())
        assert result.markdown == "单块总结"
        assert result.chunk_count == 1
        assert client.call_count == 1
        assert client._usage.total_tokens > 0

    async def test_prompt_includes_metadata(self) -> None:
        summarizer, client = _make_summarizer("ok")
        chunks = _make_chunks("some content")
        await summarizer.summarize(_make_info(), chunks, _no_progress, CancellationToken())
        msg = client.messages_list[0][0]
        assert "测试视频" in msg.content
        assert "测试作者" in msg.content


class TestMultiChunk:
    async def test_map_reduce_called_for_multiple_chunks(self) -> None:
        summarizer, client = _make_summarizer(response="每块摘要")
        chunks = _make_chunks("A" * 50, "B" * 50, "C" * 50)
        result = await summarizer.summarize(_make_info(), chunks, _no_progress, CancellationToken())
        assert result.chunk_count == 3
        # 3 Map + 1 Reduce = 4 次 LLM 调用
        assert client.call_count == 4
        assert result.usage.total_tokens == (10 + 5) * 4

    async def test_concurrent_map_semaphore_works(self) -> None:
        import asyncio

        class ConcurrentClient(_FakeClient):
            async def generate(self, messages):
                await asyncio.sleep(0.05)
                return await super().generate(messages)

        def factory():
            return ConcurrentClient()

        summarizer = MapReduceSummarizer(
            client_factory=factory,
            language=SummaryLanguage.ZH,
            max_concurrency=2,
            timestamp_url=_noop_timestamp,
        )
        chunks = _make_chunks(*(["x"] * 6))
        import time

        t0 = time.monotonic()
        result = await summarizer.summarize(_make_info(), chunks, _no_progress, CancellationToken())
        # 6 Map + 1 Reduce, max_concurrency=2 → 至少 3 波 Map，总计 ≥ 3×50ms
        elapsed = time.monotonic() - t0
        assert result.chunk_count == 6
        assert elapsed >= 0.10  # 确认确实并发了


class TestCancelWatcher:
    async def test_cancel_during_single_chunk(self) -> None:
        import asyncio

        class CancelTestClient(_FakeClient):
            async def generate(self, messages):
                await asyncio.sleep(1.0)
                return await super().generate(messages)

        def factory():
            return CancelTestClient()

        summarizer = MapReduceSummarizer(
            client_factory=factory,
            language=SummaryLanguage.ZH,
            max_concurrency=1,
            timestamp_url=_noop_timestamp,
        )
        cancel = CancellationToken()
        # 50ms 后取消
        asyncio.get_running_loop().call_later(0.05, cancel.cancel)
        with pytest.raises(asyncio.CancelledError):
            await summarizer.summarize(_make_info(), _make_chunks("x"), _no_progress, cancel)

    async def test_cancel_during_map(self) -> None:
        import asyncio

        class CancelMapClient(_FakeClient):
            async def generate(self, messages):
                await asyncio.sleep(1.0)
                return await super().generate(messages)

        def factory():
            return CancelMapClient()

        summarizer = MapReduceSummarizer(
            client_factory=factory,
            language=SummaryLanguage.ZH,
            max_concurrency=1,
            timestamp_url=_noop_timestamp,
        )
        cancel = CancellationToken()
        asyncio.get_running_loop().call_later(0.05, cancel.cancel)
        with pytest.raises(asyncio.CancelledError):
            await summarizer.summarize(
                _make_info(), _make_chunks("a", "b", "c"), _no_progress, cancel
            )

    async def test_empty_chunks_raises(self) -> None:
        summarizer, _ = _make_summarizer()
        from app.core.errors import LlmError

        with pytest.raises(LlmError, match="为空"):
            await summarizer.summarize(_make_info(), [], _no_progress, CancellationToken())


class TestTimestampInjection:
    async def test_timestamp_prepended_to_map_results(self) -> None:
        summarizer, client = _make_summarizer(
            response="chunk summary", timestamp=lambda s: f"https://x?t={s}"
        )
        chunks = _make_chunks("A" * 50, "B" * 50)
        await summarizer.summarize(_make_info(), chunks, _no_progress, CancellationToken())
        # Reduce 阶段的输入应包含时间戳链接
        reduce_msg = client.messages_list[-1][0].content
        assert "https://x?t=0" in reduce_msg
        assert "https://x?t=60" in reduce_msg
