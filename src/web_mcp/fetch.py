"""web-mcp 静态抓取 + HTML 主内容提取.

路径选择:
- 静态 + 整页文章 -> trafilatura (自带反 boilerplate)
- 静态但 trafilatura 拿不到主内容 (首页/产品页) -> markdownify 降级
- Playwright 渲染后 -> markdownify (trafilatura 对动态渲染的 HTML 不一定准)
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
import trafilatura
from bs4 import BeautifulSoup
from markdownify import markdownify as md

from .config import Settings, settings as _settings

logger = logging.getLogger(__name__)

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


async def static_fetch(
    url: str,
    *,
    settings: Settings | None = None,
) -> tuple[str, str, int, str]:
    """静态 HTTP 抓取 + HTML 主内容提取.

    Returns:
        (final_url, markdown, status_code, title)
    """
    cfg = settings or _settings

    async with httpx.AsyncClient(
        timeout=cfg.http_timeout,
        follow_redirects=True,
        proxy=cfg.proxy,
        headers={"User-Agent": cfg.user_agent},
        http2=True,
    ) as client:
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

    return final_url, extracted, resp.status_code, title


def html_to_markdown(html: str) -> str:
    """渲染后的 HTML 走这条路径: BeautifulSoup 去噪 + markdownify."""
    cleaned = _strip_noise(html)
    return md(cleaned, heading_style="ATX", bullets="-")


def extract_links(markdown: str) -> list[str]:
    """从已转换的 markdown 里抽 [text](url) 形式的链接.
    简单启发式, 不要求 100% 准确.
    """
    import re

    pattern = r"\[([^\]]*)\]\((https?://[^\s)]+)\)"
    return list({m.group(2) for m in re.finditer(pattern, markdown)})


# 公开的对外类型别名
FetchResult = tuple[str, str, int, str]  # final_url, markdown, status, title
