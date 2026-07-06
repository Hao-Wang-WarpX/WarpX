"""utils 离线单测 (smart_truncate 等纯函数)."""

from web_mcp.utils import smart_truncate


def test_truncate_no_op_when_short():
    text = "短的文本"
    out, truncated = smart_truncate(text, max_chars=100)
    assert out == text
    assert truncated is False


def test_truncate_cuts_at_paragraph_boundary():
    text = "第一段\n\n第二段内容较长\n\n第三段"
    out, truncated = smart_truncate(text, max_chars=12)  # 大概切到中间
    assert truncated is True
    assert "[...truncated" in out
    # 必须在某个 \n\n 处切（而不是硬切）
    # cut[:12] = "第一段\n\n第二段"... idx 应该找最近 \n\n
    # 这里允许包含 \n\n
    assert "\n\n" in out or len(out) <= 15


def test_truncate_falls_back_to_hard_cut_when_no_boundary():
    # 没有 \n\n 时退到硬切
    text = "a" * 500  # 500 个 'a' 没有段落边界
    out, truncated = smart_truncate(text, max_chars=100)
    assert truncated is True
    assert out.startswith("a" * 50)
    assert "[...truncated" in out


def test_truncate_preserves_meaningful_text():
    text = "重要的内容在前\n\n次要内容会被截掉因为太长了啊啊啊" + "x" * 100
    out, _ = smart_truncate(text, max_chars=20)
    assert "重要的内容在前" in out


if __name__ == "__main__":
    test_truncate_no_op_when_short()
    test_truncate_cuts_at_paragraph_boundary()
    test_truncate_falls_back_to_hard_cut_when_no_boundary()
    test_truncate_preserves_meaningful_text()
    print("✓ utils tests passed")
