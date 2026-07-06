"""web-mcp 工具函数: smart_truncate 和其他共用 helper."""

from __future__ import annotations


def smart_truncate(text: str, max_chars: int) -> tuple[str, bool]:
    """截断 text 到 max_chars, 优先在段落边界 (\n\n) 切.

    Returns:
        (截断后的文本, 是否实际被截断)
    """
    if len(text) <= max_chars:
        return text, False
    cut = text[:max_chars]
    idx = cut.rfind("\n\n")
    if idx > max_chars * 0.6:
        return (
            cut[:idx] + f"\n\n[...truncated, original {len(text)} chars]",
            True,
        )
    return cut + "\n\n[...truncated]", True
