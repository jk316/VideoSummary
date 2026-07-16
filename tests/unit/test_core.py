"""core 模型/事件/取消/异常 单元测试。"""

import dataclasses

import pytest

from app.core.cancellation import CancellationToken
from app.core.errors import ConfigError, LlmError, TaskCancelled, VideoSummaryError
from app.core.models import (
    Segment,
    Site,
    SubtitleTrack,
    TokenUsage,
    Transcript,
    TranscriptSource,
    VideoInfo,
    VideoRef,
)


def _make_transcript() -> Transcript:
    return Transcript(
        language="zh",
        source=TranscriptSource.SUBTITLE,
        segments=(
            Segment(start=0.0, end=2.5, text="第一句"),
            Segment(start=2.5, end=5.0, text="第二句"),
        ),
    )


class TestTranscript:
    def test_text_joins_segments_by_line(self) -> None:
        assert _make_transcript().text == "第一句\n第二句"

    def test_dict_roundtrip(self) -> None:
        transcript = _make_transcript()
        assert Transcript.from_dict(transcript.to_dict()) == transcript

    def test_from_dict_rejects_bad_source(self) -> None:
        data = _make_transcript().to_dict()
        data["source"] = "telepathy"
        with pytest.raises(ValueError):
            Transcript.from_dict(data)


class TestVideoModels:
    def test_video_info_dict_roundtrip(self) -> None:
        info = VideoInfo(
            ref=VideoRef(site=Site.BILIBILI, video_id="BV1xx411c7mD", url="https://b23.tv/x"),
            title="测试视频",
            duration=61.5,
            author="up主",
        )
        assert VideoInfo.from_dict(info.to_dict()) == info

    def test_video_ref_optional_fields_default_none(self) -> None:
        ref = VideoRef.from_dict({"site": "youtube", "video_id": "abc", "url": "https://y.tb/abc"})
        assert ref.title is None
        assert ref.playlist_index is None

    def test_subtitle_track_dict_roundtrip(self) -> None:
        track = SubtitleTrack(lang="zh-Hans", is_auto=True, format="vtt")
        assert SubtitleTrack.from_dict(track.to_dict()) == track

    def test_models_are_frozen(self) -> None:
        transcript = _make_transcript()
        with pytest.raises(dataclasses.FrozenInstanceError):
            transcript.language = "en"  # type: ignore[misc]


class TestTokenUsage:
    def test_add_returns_new_instance(self) -> None:
        a = TokenUsage(prompt_tokens=10, completion_tokens=5)
        b = TokenUsage(prompt_tokens=1, completion_tokens=2)
        total = a + b
        assert total == TokenUsage(prompt_tokens=11, completion_tokens=7)
        assert a == TokenUsage(prompt_tokens=10, completion_tokens=5)  # 未被修改

    def test_total_tokens(self) -> None:
        assert TokenUsage(prompt_tokens=3, completion_tokens=4).total_tokens == 7


class TestCancellationToken:
    def test_initial_state_not_cancelled(self) -> None:
        token = CancellationToken()
        assert not token.is_cancelled()
        token.raise_if_cancelled()  # 不应抛出

    def test_cancel_then_raise(self) -> None:
        token = CancellationToken()
        token.cancel()
        assert token.is_cancelled()
        with pytest.raises(TaskCancelled):
            token.raise_if_cancelled()


class TestErrors:
    def test_default_user_message_per_class(self) -> None:
        assert "API" in LlmError("timeout").user_message
        assert ConfigError("bad").user_message != VideoSummaryError("x").user_message

    def test_custom_user_message_overrides_default(self) -> None:
        err = LlmError("401", user_message="API Key 无效")
        assert err.user_message == "API Key 无效"
        assert str(err) == "401"

    def test_task_cancelled_is_not_business_error(self) -> None:
        assert not issubclass(TaskCancelled, VideoSummaryError)
