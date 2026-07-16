"""proxy 解析与文件名清洗 单元测试。"""

import pytest

from app.utils.proxy import resolve_httpx_proxy, resolve_ytdlp_proxy
from app.utils.sanitize import sanitize_filename


class TestResolveYtdlpProxy:
    def test_explicit_proxy_wins(self) -> None:
        assert resolve_ytdlp_proxy("http://127.0.0.1:7890", True) == "http://127.0.0.1:7890"

    def test_system_proxy_returns_none(self) -> None:
        assert resolve_ytdlp_proxy("", True) is None

    def test_disabled_forces_direct(self) -> None:
        assert resolve_ytdlp_proxy("", False) == ""


class TestResolveHttpxProxy:
    def test_explicit_proxy_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HTTPS_PROXY", "http://env:1")
        assert resolve_httpx_proxy("socks5://x:2", True) == "socks5://x:2"

    def test_system_proxy_reads_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for name in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv("HTTP_PROXY", "http://env:8080")
        assert resolve_httpx_proxy("", True) == "http://env:8080"

    def test_disabled_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HTTPS_PROXY", "http://env:1")
        assert resolve_httpx_proxy("", False) is None


class TestSanitizeFilename:
    def test_invalid_chars_replaced(self) -> None:
        assert sanitize_filename('a<b>:c"/d\\e|f?g*h') == "a_b__c__d_e_f_g_h"

    def test_trailing_dots_and_spaces_stripped(self) -> None:
        assert sanitize_filename("视频标题... ") == "视频标题"

    def test_reserved_name_prefixed(self) -> None:
        assert sanitize_filename("CON") == "_CON"
        assert sanitize_filename("con.txt") == "_con.txt"

    def test_empty_falls_back(self) -> None:
        assert sanitize_filename("???") == "untitled"
        assert sanitize_filename("  ") == "untitled"

    def test_long_name_truncated(self) -> None:
        assert len(sanitize_filename("很长" * 200)) <= 120

    def test_whitespace_collapsed(self) -> None:
        assert sanitize_filename("a   b\t\nc") == "a b c"
