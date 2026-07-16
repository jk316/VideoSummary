"""Map-Reduce 总结器：单 chunk 直通，多 chunk 并发 Map → Reduce。

依赖 Abstract Summarizer，注入 LLMClientFactory（每 asyncio.run() 周期创建新客户端），
取消 watcher 每 0.5s 轮询 CancellationToken，触发即 cancel 全部 in-flight LLM 调用。
"""

import asyncio
import contextlib
import logging
from abc import ABC, abstractmethod
from collections.abc import Callable

from app.core.cancellation import CancellationToken
from app.core.errors import LlmError
from app.core.events import ProgressFn
from app.core.models import Chunk, SummaryLanguage, SummaryResult, TokenUsage, VideoInfo
from app.llm.base import LLMClientFactory, Message
from app.summarizer.prompts import get_default_prompts

logger = logging.getLogger(__name__)

TimestampUrlFn = Callable[[int], str]
"""秒数 → 时间戳跳转 URL。"""

_CANCEL_POLL_SECONDS = 0.5


class Summarizer(ABC):
    """总结器抽象：Pipeline 注入此接口，实现可替换。"""

    @abstractmethod
    async def summarize(
        self,
        info: VideoInfo,
        chunks: list[Chunk],
        progress: ProgressFn,
        cancel: CancellationToken,
    ) -> SummaryResult: ...


class MapReduceSummarizer(Summarizer):
    """Map-Reduce 总结实现。"""

    def __init__(
        self,
        client_factory: LLMClientFactory,
        language: SummaryLanguage,
        max_concurrency: int,
        timestamp_url: TimestampUrlFn,
        map_prompt: str = "",
        reduce_prompt: str = "",
    ) -> None:
        self._client_factory = client_factory
        self._language = language
        self._semaphore = asyncio.Semaphore(max_concurrency)
        prompts = get_default_prompts(str(language))
        self._map_prompt = map_prompt or prompts["map"]
        self._reduce_prompt = reduce_prompt or prompts["reduce"]
        self._timestamp_url_cb = timestamp_url

    async def summarize(
        self,
        info: VideoInfo,
        chunks: list[Chunk],
        progress: ProgressFn,
        cancel: CancellationToken,
    ) -> SummaryResult:
        if not chunks:
            raise LlmError("chunks 为空", user_message="暂无可总结的内容。")
        client = self._client_factory()
        try:
            if len(chunks) == 1:
                progress(None, "生成总结…")
                text, map_usage = await self._summarize_single(client, info, chunks[0], cancel)
                progress(0.9, None)
                return SummaryResult(
                    markdown=text,
                    language=self._language,
                    chunk_count=1,
                    usage=map_usage,
                    elapsed_seconds=0.0,
                )
            return await self._map_reduce(client, info, chunks, progress, cancel)
        finally:
            await client.aclose()

    # ------------------------------------------------------------ Map-Reduce

    async def _map_reduce(
        self,
        client: object,
        info: VideoInfo,
        chunks: list[Chunk],
        progress: ProgressFn,
        cancel: CancellationToken,
    ) -> SummaryResult:
        import time

        start_time = time.monotonic()
        tasks = [self._map_one(client, info, chunk, cancel) for chunk in chunks]
        map_results = await _gather_with_cancel(tasks, cancel)
        progress(0.7, "汇总各片段摘要…")
        chunk_texts = [
            f"## 片段 {i + 1}（{_fmt_seconds(c.start)} — {_fmt_seconds(c.end)}）\n{text}"
            for i, (c, (text, _)) in enumerate(zip(chunks, map_results, strict=True))
        ]
        reduce_input = "\n\n---\n\n".join(chunk_texts)
        reduce_prompt = self._reduce_prompt.format(
            title=info.title,
            author=info.author,
            duration=_fmt_seconds(info.duration),
            chunk_summaries=reduce_input,
        )
        final_text, reduce_usage = await self._llm_generate(
            client, [Message(role="user", content=reduce_prompt)], cancel
        )
        map_usages = [u for _, u in map_results]
        total_usage = sum(map_usages, TokenUsage()) + reduce_usage
        progress(1.0, "总结完成")
        return SummaryResult(
            markdown=final_text,
            language=self._language,
            chunk_count=len(chunks),
            usage=total_usage,
            elapsed_seconds=time.monotonic() - start_time,
        )

    async def _map_one(
        self, client: object, info: VideoInfo, chunk: Chunk, cancel: CancellationToken
    ) -> tuple[str, TokenUsage]:
        async with self._semaphore:
            prompt = self._map_prompt.format(
                title=info.title,
                author=info.author,
                start=_fmt_seconds(chunk.start),
                end=_fmt_seconds(chunk.end),
                chunk_text=chunk.text,
            )
            text, usage = await self._llm_generate(
                client, [Message(role="user", content=prompt)], cancel
            )
            ts_link = self._timestamp_url(chunk.start)
            prefix = f"[▶ {_fmt_seconds(chunk.start)}]({ts_link})  " if ts_link else ""
            return prefix + text, usage

    # ---------------------------------------------------------------- 单块

    async def _summarize_single(
        self, client: object, info: VideoInfo, chunk: Chunk, cancel: CancellationToken
    ) -> tuple[str, TokenUsage]:
        prompt = self._reduce_prompt.format(
            title=info.title,
            author=info.author,
            duration=_fmt_seconds(info.duration),
            chunk_summaries=(
                f"## 片段 1（{_fmt_seconds(chunk.start)} — {_fmt_seconds(chunk.end)}）\n"
                f"{chunk.text}"
            ),
        )
        return await self._llm_generate(client, [Message(role="user", content=prompt)], cancel)

    # ---------------------------------------------------------------- LLM

    async def _llm_generate(
        self, client: object, messages: list[Message], cancel: CancellationToken
    ) -> tuple[str, TokenUsage]:
        response = await _run_with_cancel(client.generate(messages), cancel)
        # 在文本中补充时间戳链接（Map 阶段各 chunk 统一后处理）
        return response.text, response.usage

    def _timestamp_url(self, seconds: int) -> str:
        try:
            return self._timestamp_url_cb(seconds)
        except Exception:
            return ""


def _fmt_seconds(total: float) -> str:
    """秒数 → HH:MM:SS 或 MM:SS。"""
    t = max(int(total), 0)
    hours, remainder = divmod(t, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


async def _run_with_cancel(coro, cancel: CancellationToken):
    """在执行期间轮询取消令牌；触发时 cancel 协程并关闭 client。"""
    task = asyncio.create_task(coro)

    async def watcher():
        while not task.done():
            await asyncio.sleep(_CANCEL_POLL_SECONDS)
            if cancel.is_cancelled():
                task.cancel()

    watcher_task = asyncio.create_task(watcher())
    try:
        return await task
    finally:
        watcher_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await watcher_task


async def _gather_with_cancel(coros, cancel: CancellationToken) -> list:
    """并发执行并收集结果；被取消时 cancel 全部未完成任务。"""
    tasks = [asyncio.create_task(c) for c in coros]
    if not tasks:
        return []

    async def watcher():
        while not all(t.done() for t in tasks):
            await asyncio.sleep(_CANCEL_POLL_SECONDS)
            if cancel.is_cancelled():
                for t in tasks:
                    t.cancel()

    watcher_task = asyncio.create_task(watcher())
    try:
        return await asyncio.gather(*tasks)
    finally:
        watcher_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await watcher_task
