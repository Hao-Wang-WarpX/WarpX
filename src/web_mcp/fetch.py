"""web-mcp 静态抓取 + HTML 主内容提取.

路径选择:
- 静态 + 整页文章 -> trafilatura (自带反 boilerplate)
- 静态但 trafilatura 拿不到主内容 (首页/产品页) -> markdownify 降级
- Playwright 渲染后 -> markdownify (trafilatura 对动态渲染的 HTML 不一定准)
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

import httpx
import tenacity
import trafilatura
from bs4 import BeautifulSoup
from markdownify import markdownify as md

from .config import Settings, settings as _settings

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# 链接提取 & 剥离
# --------------------------------------------------------------------------- #

# [text](url) 模式 — 简单启发式, 不要求 100% 准确
_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\((https?://[^\s)]+)\)")


def extract_links(markdown: str) -> list[str]:
    """从已转换的 markdown 里抽 [text](url) 形式的链接. 去重返回."""
    return list({m.group(2) for m in _MD_LINK_RE.finditer(markdown)})


def _strip_markdown_links(markdown: str) -> str:
    """移除 markdown 中的 [text](url) 链接, 只保留链接文本.

    - [text](url)  → text
    - ![alt](url)  → 保留原样 (图片语法, 去掉也看不懂)
    """
    # 只替换非图片链接 (! 开头的不动)
    return re.sub(r"(?<!!)\[([^\]]*)\]\(https?://[^\s)]+\)", r"\1", markdown)


# --------------------------------------------------------------------------- #
# HTML 清理
# --------------------------------------------------------------------------- #

# 要从 HTML 里整块删除的标签 (含内容)
_DROP_TAGS = [
    "script",
    "style",
    "noscript",
    "iframe",
    "svg",
    "form",
    "button",
    "nav",
    "footer",
    "aside",
    "header",
]


def _strip_noise(html: str) -> str:
    """用 BeautifulSoup 真正去掉噪声标签及其内容 (markdownify 的 strip 只去 tag)."""
    soup = BeautifulSoup(html, "lxml")
    for tag in _DROP_TAGS:
        for el in soup.find_all(tag):
            el.decompose()
    return str(soup)


# --------------------------------------------------------------------------- #
# 共享 HTTP 客户端 (复用连接池)
# --------------------------------------------------------------------------- #

_client: httpx.AsyncClient | None = None
_client_lock = asyncio.Lock()


async def _get_client(settings: Settings) -> httpx.AsyncClient:
    """懒初始化并返回共享的 httpx.AsyncClient.

    并发安全; proxy/UA 变化不会重建 (运行时不变).
    """
    global _client
    if _client is None or _client.is_closed:
        async with _client_lock:
            if _client is None or _client.is_closed:
                _client = httpx.AsyncClient(
                    timeout=settings.http_timeout,
                    follow_redirects=True,
                    proxy=settings.proxy,
                    headers={"User-Agent": settings.user_agent},
                    http2=True,
                    limits=httpx.Limits(
                        max_connections=20,
                        max_keepalive_connections=10,
                    ),
                )
    return _client


async def close_http_client() -> None:
    """在 lifespan shutdown 时调用, 关闭共享 HTTP 客户端."""
    global _client
    async with _client_lock:
        if _client and not _client.is_closed:
            await _client.aclose()
            _client = None


# --------------------------------------------------------------------------- #
# 主抓取逻辑
# --------------------------------------------------------------------------- #


async def static_fetch(
    url: str,
    *,
    settings: Settings | None = None,
) -> tuple[str, str, int, str]:
    """静态 HTTP 抓取 + HTML 主内容提取.

    Returns:
        (final_url, markdown, status_code, title)

    瞬时网络错误自动重试 (最多 2 次, 指数退避).
    """
    cfg = settings or _settings
    client = await _get_client(cfg)

    @tenacity.retry(
        stop=tenacity.stop_after_attempt(3),
        wait=tenacity.wait_exponential(multiplier=0.5, min=0.5, max=4),
        retry=tenacity.retry_if_exception_type(httpx.HTTPError),
        reraise=True,
    )
    async def _do_fetch() -> tuple[httpx.Response, str, str, str | None]:
        resp = await client.get(url)
        resp.raise_for_status()
        raw_html = resp.text
        final_url = str(resp.url)

        # 1. 先试 trafilatura (它在主内容提取上比 markdownify 强很多)
        extracted = trafilatura.extract(
            raw_html,
            output_format="markdown",
            include_links=True,
            include_images=False,
            include_tables=True,
            favor_precision=True,
            with_metadata=False,
            url=final_url,
        )

        title = ""
        meta = trafilatura.extract_metadata(raw_html)
        if meta and meta.title:
            title = meta.title.strip()

        if not extracted or len(extracted) < 150:
            # 首页/列表页/产品页等没有"主文章", 降级用 markdownify
            cleaned = _strip_noise(raw_html)
            extracted = md(cleaned, heading_style="ATX", bullets="-")

        return resp, final_url, extracted, title

    try:
        resp, final_url, markdown, title = await _do_fetch()
        return final_url, markdown, resp.status_code, title or ""
    except tenacity.RetryError as e:
        # tenacity 会把最后一次异常包进 RetryError
        last = e.last_attempt.exception() if e.last_attempt else None
        raise last or e
    except httpx.HTTPError:
        raise
    except Exception:
        raise


# --------------------------------------------------------------------------- #
# HTML → Markdown (Playwright 渲染后使用)
# --------------------------------------------------------------------------- #


def html_to_markdown(html: str, *, include_links: bool = True) -> str:
    """渲染后的 HTML 走这条路径: BeautifulSoup 去噪 + markdownify.

    include_links=False 时会在转换后移除 [text](url) 链接.
    """
    cleaned = _strip_noise(html)
    result = md(cleaned, heading_style="ATX", bullets="-")
    if not include_links:
        result = _strip_markdown_links(result)
    return result


# --------------------------------------------------------------------------- #
# 类型别名
# --------------------------------------------------------------------------- #

# 公开的对外类型别名
FetchResult = tuple[str, str, int, str]  # final_url, markdown, status, title
