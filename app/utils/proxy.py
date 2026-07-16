"""代理解析：显式配置优先于系统代理；供 yt-dlp / httpx / HF 下载共用。

参数使用普通值而非 NetworkConfig，避免 utils 与 config 的横向耦合。
"""

import os


def resolve_ytdlp_proxy(proxy: str, use_system_proxy: bool) -> str | None:
    """解析 yt-dlp ``--proxy`` 参数值。

    Returns:
        显式代理地址；``None`` 表示不传参数（yt-dlp 自行继承系统/环境代理）；
        空字符串表示强制直连（``--proxy ""``）。
    """
    if proxy:
        return proxy
    return None if use_system_proxy else ""


def resolve_httpx_proxy(proxy: str, use_system_proxy: bool) -> str | None:
    """解析 httpx / huggingface_hub 场景的代理 URL。

    Returns:
        代理 URL；``None`` 表示直连。
    """
    if proxy:
        return proxy
    if use_system_proxy:
        for name in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
            value = os.environ.get(name)
            if value:
                return value
    return None
