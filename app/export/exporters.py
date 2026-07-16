"""导出器：Transcript → txt/json/srt、SummaryResult → md/txt/json。"""

import json as json_module
from pathlib import Path

from app.core.errors import ExportError
from app.core.models import SummaryResult, Transcript


def export_transcript_txt(transcript: Transcript, path: Path) -> Path:
    """纯文本：段落间双换行。"""
    blocks = [s.text for s in transcript.segments]
    return _write(path, "\n\n".join(blocks))


def export_transcript_json(transcript: Transcript, path: Path) -> Path:
    """结构化 JSON。"""
    return _write_json(path, transcript.to_dict())


def export_transcript_srt(transcript: Transcript, path: Path) -> Path:
    """SRT 字幕格式。"""
    lines: list[str] = []
    for i, seg in enumerate(transcript.segments, start=1):
        lines.append(str(i))
        lines.append(f"{_srt_ts(seg.start)} --> {_srt_ts(seg.end)}")
        lines.append(seg.text)
        lines.append("")
    return _write(path, "\n".join(lines))


def export_summary_md(result: SummaryResult, path: Path) -> Path:
    """Markdown 原样写入。"""
    return _write(path, result.markdown)


def export_summary_txt(result: SummaryResult, path: Path) -> Path:
    """纯文本：剥离常见 Markdown 标记（** ## - * 等前缀）。"""
    clean = result.markdown
    # 基础清洗，保留可读性
    clean = clean.replace("**", "")
    clean = clean.replace("## ", "")
    clean = clean.replace("* ", "• ")
    clean = clean.replace("- ", "• ")
    return _write(path, clean.strip() + "\n")


def export_summary_json(result: SummaryResult, path: Path) -> Path:
    """结构化 JSON（含 usage 与 chunk_count）。"""
    return _write_json(
        path,
        {
            "markdown": result.markdown,
            "language": str(result.language),
            "chunk_count": result.chunk_count,
            "usage": {
                "prompt_tokens": result.usage.prompt_tokens,
                "completion_tokens": result.usage.completion_tokens,
                "total_tokens": result.usage.total_tokens,
            },
            "elapsed_seconds": result.elapsed_seconds,
        },
    )


# ---------------------------------------------------------------- 内部

_SRT_FMT = "{:02d}:{:02d}:{:02d},{:03d}"


def _srt_ts(total: float) -> str:
    t = int(max(total, 0.0) * 1000)
    hours, rem = divmod(t, 3600000)
    minutes, rem = divmod(rem, 60000)
    secs, millis = divmod(rem, 1000)
    return _SRT_FMT.format(hours, minutes, secs, millis)


def _write(path: Path, content: str) -> Path:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path
    except OSError as exc:
        raise ExportError(
            f"写入文件失败: {path}: {exc}",
            user_message="导出文件失败，请检查磁盘空间与权限。",
        ) from exc


def _write_json(path: Path, data: object) -> Path:
    return _write(path, json_module.dumps(data, ensure_ascii=False, indent=2))
