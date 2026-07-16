"""token 计数：tiktoken 优先，不可用时回退启发式估算。

tiktoken 首次使用会联网下载 BPE 文件；打包阶段随包附带并设
``TIKTOKEN_CACHE_DIR``（architecture.md §11）。运行期若 BPE 不可用
（断网且无缓存），回退启发式估算，保证 CHUNK 阶段始终可用。
"""

import logging
import math
import os
import re
from pathlib import Path
from typing import Protocol

logger = logging.getLogger(__name__)

DEFAULT_ENCODING = "cl100k_base"
_CHARS_PER_TOKEN_NON_CJK = 4
_CJK_RE = re.compile(r"[　-鿿가-힯豈-﫿぀-ヿ]")


class TokenCounter(Protocol):
    """文本 token 计数协议。"""

    def count(self, text: str) -> int: ...


class HeuristicTokenCounter:
    """无 tiktoken 时的估算：CJK 每字 ≈1 token，其余每 4 字符 ≈1 token。"""

    def count(self, text: str) -> int:
        if not text:
            return 0
        cjk = len(_CJK_RE.findall(text))
        other = len(text) - cjk
        return cjk + (math.ceil(other / _CHARS_PER_TOKEN_NON_CJK) if other else 0)


class TiktokenCounter:
    """tiktoken 精确计数。"""

    def __init__(self, encoding: object) -> None:
        self._encoding = encoding

    def count(self, text: str) -> int:
        return len(self._encoding.encode(text))  # type: ignore[attr-defined]


def get_token_counter(
    encoding_name: str = DEFAULT_ENCODING, cache_dir: Path | None = None
) -> TokenCounter:
    """获取计数器；tiktoken/BPE 不可用时回退启发式并记录告警。

    Args:
        cache_dir: BPE 缓存目录（持久化到用户目录，避免每次联网）；
            已设置 TIKTOKEN_CACHE_DIR 环境变量时不覆盖。
    """
    if cache_dir is not None and "TIKTOKEN_CACHE_DIR" not in os.environ:
        os.environ["TIKTOKEN_CACHE_DIR"] = str(cache_dir)
    try:
        import tiktoken

        return TiktokenCounter(tiktoken.get_encoding(encoding_name))
    except Exception as exc:  # ImportError 或 BPE 下载失败
        logger.warning("tiktoken 不可用（%s），回退启发式 token 估算", exc)
        return HeuristicTokenCounter()
