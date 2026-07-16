"""cache/manager 单元测试。"""

from pathlib import Path

from app.cache.manager import CacheManager, VideoCache, make_summary_key, sha256_text


def _default_key_args() -> dict:
    return {
        "transcript_sha": sha256_text("hello"),
        "model": "gpt-4o-mini",
        "prompt_text": "请总结 {chunk_text}",
        "language": "zh",
        "chunk_max_tokens": 3000,
        "chunk_overlap_tokens": 200,
    }


class TestSummaryKey:
    def test_key_is_stable_and_short(self) -> None:
        key1 = make_summary_key(**_default_key_args())
        key2 = make_summary_key(**_default_key_args())
        assert key1 == key2
        assert len(key1) == 12

    def test_any_input_change_changes_key(self) -> None:
        base = make_summary_key(**_default_key_args())
        variants = [
            {"transcript_sha": sha256_text("world")},
            {"model": "deepseek-chat"},
            {"prompt_text": "换个模板 {chunk_text}"},
            {"language": "en"},
            {"chunk_max_tokens": 2000},
            {"chunk_overlap_tokens": 100},
        ]
        for override in variants:
            assert make_summary_key(**{**_default_key_args(), **override}) != base


class TestVideoCache:
    def test_atomic_write_and_read(self, tmp_path: Path) -> None:
        cache = VideoCache(tmp_path / "youtube_abc")
        cache.write_text("transcript.json", '{"language": "zh"}')
        assert cache.exists("transcript.json")
        assert cache.read_text("transcript.json") == '{"language": "zh"}'
        assert not cache.path("transcript.json.tmp").exists()

    def test_half_written_tmp_is_not_a_hit(self, tmp_path: Path) -> None:
        root = tmp_path / "youtube_abc"
        root.mkdir(parents=True)
        (root / "audio.wav.tmp").write_bytes(b"partial")
        cache = VideoCache(root)
        assert not cache.exists("audio.wav")
        assert not (root / "audio.wav.tmp").exists()  # 初始化时清理遗留 tmp

    def test_delete_missing_is_noop(self, tmp_path: Path) -> None:
        cache = VideoCache(tmp_path / "v")
        cache.delete("nonexistent.txt")  # 不应抛出


class TestCacheManager:
    def test_for_video_sanitizes_key(self, tmp_path: Path) -> None:
        manager = CacheManager(tmp_path)
        cache = manager.for_video("youtube", "a/b:c*d")
        assert cache.root.parent == tmp_path
        assert "/" not in cache.root.name[len("youtube_") :]
        assert cache.root.is_dir()

    def test_total_size_by_category(self, tmp_path: Path) -> None:
        manager = CacheManager(tmp_path)
        cache = manager.for_video("youtube", "abc")
        cache.write_bytes("audio.wav", b"x" * 100)
        cache.write_text("subtitle.zh.vtt", "WEBVTT")
        cache.write_text("transcript.json", "{}")
        cache.write_text("meta.json", "{}")
        cache.write_text("summary.abc123.md", "# 总结")
        cache.write_text("chunks/0.abc123.md", "chunk 摘要")
        sizes = manager.total_size()
        assert sizes["audio"] == 100
        assert all(sizes[k] > 0 for k in ("subtitle", "transcript", "meta", "summary"))
        assert sizes["total"] == sum(v for k, v in sizes.items() if k != "total")

    def test_clear_removes_everything(self, tmp_path: Path) -> None:
        manager = CacheManager(tmp_path)
        manager.for_video("youtube", "abc").write_text("meta.json", "{}")
        manager.clear()
        assert list(tmp_path.iterdir()) == []
        assert manager.total_size()["total"] == 0
