"""web-mcp MCP server 入口: 注册 4 个工具 + lifespan 管理 Playwright."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any

from mcp import McpError
from mcp.server.fastmcp import FastMCP
from mcp.types import INTERNAL_ERROR, ErrorData
from pydantic import Field

from . import __version__
from .browser import manager as browser_manager
from .config import detect_and_set_proxy, settings
from .fetch import close_http_client, extract_links, static_fetch
from .images import download_image as _download_image
from .search import ddg_text_search
from .utils import smart_truncate

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Lifespan: 启动时检测代理 + 初始化 Playwright (懒), 退出时清理
# --------------------------------------------------------------------------- #


@asynccontextmanager
async def lifespan(server: FastMCP):
    # 启动: 代理探测 + 日志
    detect_and_set_proxy(settings)
    logger.info(f"web-mcp v{__version__} starting up (proxy={'set' if settings.proxy else 'direct'})")
    try:
        yield {"manager": browser_manager, "settings": settings}
    finally:
        logger.info("web-mcp shutting down")
        # 顺序很重要: 先关浏览器, 再关 HTTP client
        try:
            await browser_manager.stop()
        except Exception as e:
            logger.warning(f"playwright stop failed: {e}")
        try:
            await close_http_client()
        except Exception as e:
            logger.warning(f"http client close failed: {e}")


# --------------------------------------------------------------------------- #
# FastMCP 实例
# --------------------------------------------------------------------------- #


mcp = FastMCP(
    name="web-mcp",
    instructions=(
        "本地 web 工具集: web_search / fetch_url (含 JS 渲染) / "
        "download_image / search_and_fetch. "
        "图像理解走 Claude 原生 Read 工具 (下载图片后用 Read(path)). "
        "render=True 启动慢, 优先用静态抓取; 仅当 JS 渲染站才用 render=true."
    ),
    lifespan=lifespan,
)


# --------------------------------------------------------------------------- #
# Tool 1: web_search
# --------------------------------------------------------------------------- #


@mcp.tool()
async def web_search(
    query: str = Field(..., min_length=1, max_length=500, description="搜索关键词"),
    max_results: int = Field(10, ge=1, le=30, description="返回结果数上限"),
    region: str = Field(
        default_factory=lambda: settings.ddg_region,
        description="DDG region code (wt-wt/us-en/cn-zh/jp-ja ...)",
    ),
    safesearch: str = Field(
        default_factory=lambda: settings.ddg_safesearch,
        description="strict / moderate / off",
    ),
) -> dict[str, Any]:
    """DuckDuckGo 搜索 (无需 API key)."""
    try:
        results = await ddg_text_search(
            query=query,
            max_results=max_results,
            region=region,
            safesearch=safesearch,
            timeout=settings.ddg_timeout,
        )
        return {
            "query": query,
            "region": region,
            "count": len(results),
            "results": results,
        }
    except McpError:
        raise
    except Exception as e:
        logger.exception(f"web_search failed for query={query!r}")
        raise McpError(
            ErrorData(code=INTERNAL_ERROR, message=f"search failed for query={query!r}: {e}")
        ) from e


# --------------------------------------------------------------------------- #
# Tool 2: fetch_url
# --------------------------------------------------------------------------- #


@mcp.tool()
async def fetch_url(
    url: str = Field(..., description="完整 URL, 含 http(s)://"),
    render: bool = Field(
        False, description="True=用 Playwright 渲染 JS 页面 (慢, 仅必要时用)"
    ),
    max_chars: int = Field(
        15000, ge=500, le=200000, description="返回 markdown 的字符数上限"
    ),
    include_links: bool = Field(True, description="是否在 markdown 里保留链接"),
    wait_selector: str | None = Field(
        None, max_length=200, description="(render=True 时) 等此 CSS 选择器出现再抓"
    ),
) -> dict[str, Any]:
    """抓取 URL 转 markdown. render=False 走静态 + trafilatura; render=True 走 Playwright."""
    try:
        rendered = render
        render_fallback = False

        if render:
            try:
                rd = await browser_manager.render(url, wait_selector=wait_selector)
                final_url = rd["final_url"]
                markdown = rd["markdown"]
                status = rd["status"]
                title = rd["title"]
                # include_links=False: 在 render 输出的 markdown 上后处理
                if not include_links:
                    from .fetch import _strip_markdown_links
                    markdown = _strip_markdown_links(markdown)
            except Exception as e:
                # 自动降级到静态抓取
                logger.warning(f"render failed, falling back to static: {e}")
                render_fallback = True
                rendered = False
                final_url, markdown, status, title = await static_fetch(url)
        else:
            final_url, markdown, status, title = await static_fetch(url)

        # include_links=False 时静态路径的 trafilatura/markdownify 可能已含链接;
        # 统一后处理确保一致性.
        if not include_links:
            from .fetch import _strip_markdown_links
            markdown = _strip_markdown_links(markdown)

        truncated_md, truncated = smart_truncate(markdown, max_chars)
        links: list[str] = []
        if include_links:
            links = extract_links(truncated_md)

        return {
            "url": url,
            "final_url": final_url,
            "status": status,
            "rendered": rendered,
            "render_fallback": render_fallback,
            "title": title,
            "markdown": truncated_md,
            "truncated": truncated,
            "char_count": len(truncated_md),
            "links": links,
        }
    except McpError:
        raise
    except Exception as e:
        logger.exception(f"fetch_url failed for url={url!r}")
        raise McpError(
            ErrorData(code=INTERNAL_ERROR, message=f"fetch failed for {url}: {e}")
        ) from e


# --------------------------------------------------------------------------- #
# Tool 3: download_image
# --------------------------------------------------------------------------- #


@mcp.tool()
async def download_image(
    url: str = Field(..., description="图片直链 URL"),
    save_path: str | None = Field(
        None, description="None = 用 download_dir + 哈希名; 指定时必须落在 download_dir 下"
    ),
    max_size_mb: float = Field(
        default_factory=lambda: settings.max_image_size_mb,
        gt=0.5,
        le=100.0,
        description="图片大小上限 (MB), 超过直接拒",
    ),
) -> dict[str, Any]:
    """下载图片到本地, 返回绝对路径. Claude 用 Read(path) 即可看图."""
    try:
        # 用 model_copy 避免重新跑 pydantic validators
        effective = (
            settings
            if max_size_mb == settings.max_image_size_mb
            else settings.model_copy(update={"max_image_size_mb": max_size_mb})
        )

        result = await _download_image(url, save_path=save_path, settings=effective)
        # 给 Claude 一句明确指引
        result["next_step"] = (
            f"图片已保存到 {result['path']}, 用 Read 工具即可查看."
        )
        return result
    except McpError:
        raise
    except ValueError as e:
        raise McpError(ErrorData(code=INTERNAL_ERROR, message=str(e))) from e
    except Exception as e:
        logger.exception(f"download_image failed for url={url!r}")
        raise McpError(
            ErrorData(code=INTERNAL_ERROR, message=f"download failed for {url}: {e}")
        ) from e


# --------------------------------------------------------------------------- #
# Tool 4: search_and_fetch
# --------------------------------------------------------------------------- #


@mcp.tool()
async def search_and_fetch(
    query: str = Field(..., min_length=1, max_length=500, description="搜索关键词"),
    max_results: int = Field(3, ge=1, le=5, description="实际 fetch 几个页面"),
    max_chars_per_page: int = Field(
        8000, ge=500, le=50000, description="每页 markdown 字符上限"
    ),
    region: str = Field(
        default_factory=lambda: settings.ddg_region, description="DDG region code"
    ),
    safesearch: str = Field(
        default_factory=lambda: settings.ddg_safesearch,
        description="strict / moderate / off",
    ),
    render: bool = Field(False, description="是否对所有页面用 Playwright 渲染"),
) -> dict[str, Any]:
    """一次调用: 搜 → 取前 N 个 → 返回每页 markdown. 单页失败不影响其他页."""

    async def fetch_one(u: str) -> dict[str, Any]:
        try:
            r = await fetch_url.fn(  # type: ignore[attr-defined]
                url=u,
                render=render,
                max_chars=max_chars_per_page,
                include_links=False,
            )
            return r
        except McpError as e:
            return {"url": u, "error": "fetch_failed", "detail": str(e)}
        except Exception as e:
            return {"url": u, "error": "fetch_failed", "detail": repr(e)}

    try:
        results = await ddg_text_search(
            query=query,
            max_results=max_results,
            region=region,
            safesearch=safesearch,
            timeout=settings.ddg_timeout,
        )
        urls = [r["href"] for r in results if r.get("href")]

        pages = await asyncio.gather(*[fetch_one(u) for u in urls])

        return {
            "query": query,
            "region": region,
            "search_count": len(results),
            "pages": pages,
        }
    except McpError:
        raise
    except Exception as e:
        logger.exception(f"search_and_fetch failed for query={query!r}")
        raise McpError(
            ErrorData(code=INTERNAL_ERROR, message=f"search_and_fetch failed: {e}")
        ) from e


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
