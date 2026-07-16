"""导出器单元测试：Transcript/SummaryResult → txt/json/srt/md。"""

import json
from pathlib import Path

import pytest

from app.core.errors import ExportError
from app.core.models import (
    Segment,
    SummaryLanguage,
    SummaryResult,
    TokenUsage,
    Transcript,
    TranscriptSource,
)
from app.export.exporters import (
    export_summary_json,
    export_summary_md,
    export_summary_txt,
    export_transcript_json,
    export_transcript_srt,
    export_transcript_txt,
)


def _transcript() -> Transcript:
    return Transcript(
        language="zh",
        source=TranscriptSource.SUBTITLE,
        segments=(
            Segment(start=0.0, end=2.5, text="第一句"),
            Segment(start=2.5, end=5.0, text="第二句\n带换行"),
        ),
    )


def _summary() -> SummaryResult:
    return SummaryResult(
        markdown="## 总结\n\n**要点** 内容\n- 项目一\n- 项目二",
        language=SummaryLanguage.ZH,
        chunk_count=1,
        usage=TokenUsage(prompt_tokens=100, completion_tokens=50),
        elapsed_seconds=3.5,
    )


class TestExportTranscript:
    def test_txt_joins_paragraphs(self, tmp_path: Path) -> None:
        path = export_transcript_txt(_transcript(), tmp_path / "out.txt")
        content = path.read_text(encoding="utf-8")
        assert content == "第一句\n\n第二句\n带换行"

    def test_json_roundtrip(self, tmp_path: Path) -> None:
        transcript = _transcript()
        path = export_transcript_json(transcript, tmp_path / "out.json")
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["language"] == "zh"
        assert data["source"] == "subtitle"
        assert len(data["segments"]) == 2
        assert data["segments"][1]["text"] == "第二句\n带换行"

    def test_srt_format(self, tmp_path: Path) -> None:
        path = export_transcript_srt(_transcript(), tmp_path / "out.srt")
        content = path.read_text(encoding="utf-8")
        assert content.startswith("1\n")
        assert "00:00:00,000 --> 00:00:02,500" in content
        assert "第一句" in content
        assert "\n2\n" in content
        assert "第二句\n带换行" in content

    def test_srt_millisecond_precision(self, tmp_path: Path) -> None:
        transcript = Transcript(
            language="en",
            source=TranscriptSource.STT,
            segments=(Segment(start=1.234, end=65.789, text="precision test"),),
        )
        path = export_transcript_srt(transcript, tmp_path / "prec.srt")
        content = path.read_text(encoding="utf-8")
        assert "00:00:01,234 --> 00:01:05,789" in content


class TestExportSummary:
    def test_md_writes_as_is(self, tmp_path: Path) -> None:
        path = export_summary_md(_summary(), tmp_path / "out.md")
        content = path.read_text(encoding="utf-8")
        assert content == _summary().markdown

    def test_txt_strips_markdown_markers(self, tmp_path: Path) -> None:
        path = export_summary_txt(_summary(), tmp_path / "out.txt")
        content = path.read_text(encoding="utf-8")
        assert "**" not in content
        assert "##" not in content
        assert "•" in content

    def test_json_includes_usage_metadata(self, tmp_path: Path) -> None:
        path = export_summary_json(_summary(), tmp_path / "out.json")
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["usage"]["prompt_tokens"] == 100
        assert data["usage"]["completion_tokens"] == 50
        assert data["usage"]["total_tokens"] == 150
        assert data["elapsed_seconds"] == 3.5
        assert data["chunk_count"] == 1

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        path = tmp_path / "deep" / "nested" / "out.md"
        export_summary_md(_summary(), path)
        assert path.is_file()


class TestExportErrors:
    def test_readonly_dir_raises_export_error(self, tmp_path: Path) -> None:
        if not _is_windows():
            # 在 Unix 上设目录只读
            tmp_path.chmod(0o444)
            try:
                with pytest.raises(ExportError, match="导出"):
                    export_summary_md(_summary(), tmp_path / "out.md")
            finally:
                tmp_path.chmod(0o755)
        else:
            # Windows 上跳过权限测试
            pytest.skip("Windows 权限行为不同")


def _is_windows() -> bool:
    import sys

    return sys.platform == "win32"
