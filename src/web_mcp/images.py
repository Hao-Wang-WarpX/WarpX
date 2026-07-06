"""web-mcp 图片下载 + Pillow 校验 + SHA-256 去重."""

from __future__ import annotations

import hashlib
import io
import logging
from pathlib import Path

import httpx
from PIL import Image, UnidentifiedImageError

from .config import Settings, settings as _settings

logger = logging.getLogger(__name__)

# MIME -> 扩展名
SAFE_EXT: dict[str, str] = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/bmp": ".bmp",
    "image/tiff": ".tiff",
}


async def download_image(
    url: str,
    save_path: str | None = None,
    *,
    settings: Settings | None = None,
) -> dict:
    """下载 URL 处的图片, Pillow 验证, SHA-256 前 16 位做文件名.

    Returns:
        dict: {url, path, size_bytes, mime, width, height, hash_sha256}

    Raises:
        ValueError: 各种预期错误 (URL 非法, 文件过大, 非图片)
        httpx.HTTPError: 网络/HTTP 错误 (由调用方处理)
    """
    cfg = settings or _settings

    base = Path(cfg.download_dir).resolve()
    base.mkdir(parents=True, exist_ok=True)
    max_bytes = cfg.max_image_size_bytes

    async with httpx.AsyncClient(
        timeout=cfg.http_timeout,
        follow_redirects=True,
        proxy=cfg.proxy,
        headers={"User-Agent": cfg.user_agent},
    ) as client:
        # 1. HEAD 探测大小 (很多服务器会拒绝 HEAD, 失败就跳过)
        content_length_hint: int | None = None
        try:
            head = await client.head(url)
            if head.status_code < 400:
                cl_raw = head.headers.get("content-length")
                if cl_raw and cl_raw.isdigit():
                    content_length_hint = int(cl_raw)
                    if content_length_hint > max_bytes:
                        raise ValueError(
                            f"image too large by HEAD: {content_length_hint} "
                            f"> {max_bytes} bytes"
                        )
        except httpx.HTTPError as e:
            logger.debug(f"HEAD failed (continuing with stream): {e}")

        # 2. 流式下载
        buf = io.BytesIO()
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            async for chunk in resp.aiter_bytes(chunk_size=8192):
                if buf.tell() + len(chunk) > max_bytes:
                    raise ValueError(
                        f"image exceeds {cfg.max_image_size_mb}MB during stream"
                    )
                buf.write(chunk)
        data = buf.getvalue()

    # 3. Pillow 验证 + 拿尺寸 + mime
    try:
        with Image.open(io.BytesIO(data)) as img:
            img.verify()  # 验证完整性
        # verify() 后 stream position 不可靠, 重新打开拿尺寸
        with Image.open(io.BytesIO(data)) as img2:
            width, height = img2.size
            fmt = (img2.format or "").upper()
            mime = Image.MIME.get(fmt, "application/octet-stream")
    except UnidentifiedImageError as e:
        raise ValueError(f"not a valid image (Pillow cannot identify format): {e}")
    except Exception as e:
        # PIL.Image.DecompressionBombError 等
        raise ValueError(f"image validation failed: {e}")

    if not mime.startswith("image/"):
        raise ValueError(f"downloaded content is not an image: mime={mime}")

    # 4. 路径决策 + 防越权
    sha = hashlib.sha256(data).hexdigest()[:16]
    if save_path:
        target = Path(save_path).resolve()
        # 安全: 必须在 base 之下
        try:
            target.relative_to(base)
        except ValueError:
            raise ValueError(
                f"save_path must be under download_dir: {target} not under {base}"
            )
    else:
        ext = SAFE_EXT.get(mime, ".bin")
        target = base / f"{sha}{ext}"

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)

    return {
        "url": url,
        "path": str(target),
        "size_bytes": len(data),
        "mime": mime,
        "width": width,
        "height": height,
        "hash_sha256": sha,
        "truncated": bool(
            content_length_hint and content_length_hint > len(data)
        ),
    }
