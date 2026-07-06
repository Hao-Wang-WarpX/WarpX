"""web-mcp 配置: 用 pydantic-settings 读 .env, 单例导出."""

from __future__ import annotations

import socket
import sys
from pathlib import Path
from typing import Optional

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_DOWNLOAD_DIR = Path("downloads")


def _default_download_dir() -> str:
    """默认下载目录: 项目根/downloads (跨平台)."""
    return str((Path(__file__).resolve().parent.parent.parent / DEFAULT_DOWNLOAD_DIR))


# --------------------------------------------------------------------------- #
# 自动代理探测
# --------------------------------------------------------------------------- #

# (port, fallback scheme). HTTP 协议靠实际探测区分.
PROXY_CANDIDATES: list[tuple[int, str]] = [
    (7890, "http"),    # Clash / verge-mihomo 默认 HTTP
    (7897, "http"),    # 一些混合代理 (mixed-port)
    (7891, "http"),    # Clash verge 备用
    (2080, "http"),    # Clash for Windows 备用
    (8888, "http"),    # mitmproxy 默认
    (10809, "socks5"), # v2rayN SOCKS5 默认
    (1080, "socks5"),  # 通用 SOCKS5
]


def _probe_port_open(port: int, timeout: float = 0.4) -> bool:
    """只探测 TCP 端口能否在超时内连上 (不区分协议)."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except (OSError, socket.timeout):
        return False


def _detect_protocol(port: int, timeout: float = 0.6) -> Optional[str]:
    """端口已开后, 发一次伪 HTTP 请求判断是不是 HTTP 代理.

    HTTP 代理会响应 HTTP/1.x 响应行; SOCKS5 不理解 HTTP 文本协议,
    可能 close connection 或不响应 (recv 会拿不到 HTTP/ 前缀).
    """
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout) as sock:
            sock.sendall(
                b"GET http://127.0.0.1/ HTTP/1.1\r\n"
                b"Host: 127.0.0.1\r\n"
                b"Connection: close\r\n"
                b"\r\n"
            )
            sock.settimeout(timeout)
            buf = b""
            # 读到第一个 \r\n 或最多 64 字节, 够看响应行
            while len(buf) < 64:
                chunk = sock.recv(64 - len(buf))
                if not chunk:
                    break
                buf += chunk
                if b"\r\n" in buf:
                    break
            if buf.startswith(b"HTTP/"):
                return "http"
            return "socks5"
    except (OSError, socket.timeout):
        return None


def detect_local_proxy(timeout_per_port: float = 0.6) -> Optional[str]:
    """扫描候选端口, 返回第一个能用的代理 URL (含 scheme).

    命中后立即返回, 不继续扫后面的端口 (前面的优先).
    """
    for port, fallback_scheme in PROXY_CANDIDATES:
        if not _probe_port_open(port, timeout=timeout_per_port):
            continue
        scheme = _detect_protocol(port, timeout=timeout_per_port) or fallback_scheme
        return f"{scheme}://127.0.0.1:{port}"
    return None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="WEB_MCP_",
        case_sensitive=False,
        extra="ignore",
    )

    # HTTP 客户端
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    )
    http_timeout: float = Field(30.0, ge=5.0, le=120.0)
    proxy: Optional[str] = None  # 显式设值时优先; 未设则下面 model_validator 自动探测

    # Playwright
    browser_timeout: float = Field(45.0, ge=10.0, le=180.0)
    browser_headless: bool = True
    browser_wait_selector: Optional[str] = None
    browser_viewport: str = "1280,720"

    # 下载
    download_dir: str = Field(default_factory=_default_download_dir)
    max_image_size_mb: float = Field(10.0, gt=0.5, le=100.0)

    # 搜索
    ddg_region: str = "wt-wt"
    ddg_safesearch: str = "moderate"

    @field_validator("ddg_safesearch")
    @classmethod
    def _validate_safesearch(cls, v: str) -> str:
        if v not in ("strict", "moderate", "off"):
            raise ValueError("safesearch must be one of: strict, moderate, off")
        return v

    @model_validator(mode="after")
    def _autodetect_proxy(self) -> "Settings":
        """proxy 未设置时扫描本机常见代理端口, 命中第一个就填上.

        "未设置" 指 None 或空字符串 (后者常见: .env 留空 / WEB_MCP_PROXY=).

        行为:
        - WEB_MCP_PROXY 显式设值 (非空) → 用用户的
        - WEB_MCP_PROXY 未设 / 留空 → 自动探测 7890/7897/7891/2080/8888/10809/1080
        - 都没命中 → proxy 保持 None (直连)
        """
        if self.proxy is None or self.proxy == "":
            # 先清回 None, 避免空串透传给下游 httpx/playwright
            self.proxy = None
            detected = detect_local_proxy()
            if detected is not None:
                self.proxy = detected
                # stderr 一行, 不污染 stdio JSON-RPC 流
                print(
                    f"[web-mcp] auto-detected proxy: {detected}",
                    file=sys.stderr,
                    flush=True,
                )
            else:
                print(
                    "[web-mcp] no local proxy detected, using direct connection",
                    file=sys.stderr,
                    flush=True,
                )
        return self

    @property
    def viewport(self) -> dict[str, int]:
        try:
            w, h = self.browser_viewport.split(",")
            return {"width": int(w), "height": int(h)}
        except Exception:
            return {"width": 1280, "height": 720}

    @property
    def max_image_size_bytes(self) -> int:
        return int(self.max_image_size_mb * 1024 * 1024)


settings = Settings()  # 全局单例 (此时自动代理探测会跑一次)
