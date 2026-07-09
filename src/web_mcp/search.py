"""web-mcp DuckDuckGo 搜索 (ddgs 9.x sync, 用 asyncio.to_thread 异步调用).

ddgs 9.x 只导出 sync DDGS; 内部用 httpx-like 同步客户端.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import tenacity
from ddgs import DDGS

from .config import settings as _settings

logger = logging.getLogger(__name__)


def _ddg_text_sync(
    query: str,
    max_results: int,
    region: str,
    safesearch: str,
    timeout: int = 15,
) -> list[dict[str, Any]]:
    """同步 DDG 文本搜索 (在 worker thread 里跑)."""
    with DDGS(timeout=timeout) as ddgs:
        raw: list[dict[str, Any]] = ddgs.text(
            query=query,
            region=region,
            safesearch=safesearch,
            max_results=max_results,
        )
    return [
        {
            "title": r.get("title", ""),
            "href": r.get("href", "") or r.get("url", ""),
            "body": r.get("body", "") or r.get("snippet", ""),
        }
        for r in raw
    ]


async def ddg_text_search(
    query: str,
    max_results: int = 10,
    region: str = "wt-wt",
    safesearch: str = "moderate",
    timeout: float | None = None,
) -> list[dict[str, Any]]:
    """异步 DDG 搜索: 把 sync DDGS 包到 thread 里避免阻塞 event loop.

    timeout 可从 settings 传入; 瞬时网络错误自动重试 (最多 2 次).
    单次失败会抛 RuntimeError 给调用方 (ToolError 包装).
    """
    effective_timeout = int(timeout or _settings.ddg_timeout)

    @tenacity.retry(
        stop=tenacity.stop_after_attempt(3),
        wait=tenacity.wait_exponential(multiplier=0.5, min=0.5, max=4),
        retry=tenacity.retry_if_exception_type(Exception),
        reraise=True,
    )
    async def _do_search() -> list[dict[str, Any]]:
        try:
            return await asyncio.to_thread(
                _ddg_text_sync, query, max_results, region, safesearch, effective_timeout
            )
        except Exception as e:
            logger.warning(f"DDG search error: {type(e).__name__}: {e}")
            raise RuntimeError(f"DuckDuckGo search failed: {e}") from e

    try:
        return await _do_search()
    except tenacity.RetryError as e:
        last = e.last_attempt.exception() if e.last_attempt else None
        raise last or e
