"""web-mcp Playwright 浏览器管理.

设计:
- 模块级 BrowserManager 单例
- start() 在 FastMCP lifespan 启动时调用, stop() 在 finally 中调用
- render() 是 async, 每次新建 page, finally 关闭 page 防泄漏
- 用 asyncio.Semaphore 限制并发 page 数 (Chromium 内存敏感)
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
)

from .config import settings as _settings
from .fetch import html_to_markdown

logger = logging.getLogger(__name__)


class BrowserManager:
    def __init__(self, max_concurrent_pages: int = 3) -> None:
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._sem = asyncio.Semaphore(max_concurrent_pages)
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        """在 lifespan 启动时调用一次. 已启动则幂等返回."""
        async with self._lock:
            if self._browser and self._browser.is_connected():
                return
            try:
                self._pw = await async_playwright().start()
                self._browser = await self._pw.chromium.launch(
                    headless=_settings.browser_headless,
                    args=[
                        "--no-sandbox",
                        "--disable-dev-shm-usage",
                        "--disable-blink-features=AutomationControlled",
                    ],
                )
                logger.info("playwright chromium launched")
            except Exception:
                # 启动失败要清理半成品状态
                if self._browser:
                    try:
                        await self._browser.close()
                    except Exception:
                        pass
                    self._browser = None
                if self._pw:
                    try:
                        await self._pw.stop()
                    except Exception:
                        pass
                    self._pw = None
                raise

    async def stop(self) -> None:
        """在 lifespan 退出时调用."""
        async with self._lock:
            try:
                if self._browser and self._browser.is_connected():
                    await self._browser.close()
            except Exception as e:
                logger.warning(f"browser close failed: {e}")
            finally:
                self._browser = None
            try:
                if self._pw:
                    await self._pw.stop()
            except Exception as e:
                logger.warning(f"playwright stop failed: {e}")
            finally:
                self._pw = None

    @property
    def is_ready(self) -> bool:
        return self._browser is not None and self._browser.is_connected()

    async def _ensure_browser(self) -> Browser:
        if self._browser is None or not self._browser.is_connected():
            await self.start()
        assert self._browser is not None
        return self._browser

    async def render(
        self,
        url: str,
        *,
        wait_selector: str | None = None,
    ) -> dict[str, Any]:
        """Playwright 渲染 URL, 返回 {html, title, final_url, status}.

        异常:
        - playwright 启动失败/连接断开 -> 抛 RuntimeError, 调用方降级到 static_fetch
        """
        browser = await self._ensure_browser()
        context: BrowserContext | None = None
        page: Page | None = None
        async with self._sem:
            try:
                context = await browser.new_context(
                    viewport=_settings.viewport,
                    user_agent=_settings.user_agent,
                    proxy={"server": _settings.proxy} if _settings.proxy else None,
                )
                page = await context.new_page()
                # 主导航超时
                nav_timeout_ms = int(_settings.browser_timeout * 1000)
                await page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=nav_timeout_ms,
                )
                # 可选: 等指定选择器出现
                if wait_selector or _settings.browser_wait_selector:
                    sel = wait_selector or _settings.browser_wait_selector
                    try:
                        await page.wait_for_selector(sel, timeout=10_000)
                    except Exception as e:
                        logger.debug(f"wait_selector {sel!r} timed out: {e}")
                # 网络空闲多等 5s 让 SPA 跑完
                try:
                    await page.wait_for_load_state("networkidle", timeout=5_000)
                except Exception:
                    pass

                html = await page.content()
                title = await page.title()
                final_url = page.url

                markdown = html_to_markdown(html)
                return {
                    "html": html,
                    "markdown": markdown,
                    "title": title,
                    "final_url": final_url,
                    "status": 200,
                }
            except Exception as e:
                # 连接断开时尝试重启浏览器, 给下一次机会
                try:
                    if self._browser and not self._browser.is_connected():
                        logger.warning("browser disconnected, will restart on next call")
                        await self.stop()
                except Exception:
                    pass
                raise RuntimeError(f"playwright render failed for {url}: {e}") from e
            finally:
                if page:
                    try:
                        await page.close()
                    except Exception:
                        pass
                if context:
                    try:
                        await context.close()
                    except Exception:
                        pass


# 模块级单例
manager = BrowserManager()


async def render_url(url: str, *, wait_selector: str | None = None) -> dict[str, Any]:
    """对外的简单调用接口."""
    return await manager.render(url, wait_selector=wait_selector)
