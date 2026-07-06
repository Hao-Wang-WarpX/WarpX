"""fetch 单测: 用本地 HTML fixture 验证 html_to_markdown 和 extract_links."""

from pathlib import Path

from web_mcp.fetch import extract_links, html_to_markdown

FIXTURE = (Path(__file__).parent / "fixtures" / "sample.html").read_text(encoding="utf-8")


def test_html_to_markdown_removes_scripts_and_styles():
    md = html_to_markdown(FIXTURE)
    assert "window.ads" not in md
    assert "color: red" not in md
    assert "字体" not in md  # style 标签内容不应该出现


def test_html_to_markdown_extracts_main_content():
    md = html_to_markdown(FIXTURE)
    # main/article 里的内容应该保留
    assert "Python 入门指南" in md
    assert "变量" in md or "变量" in md
    assert "函数" in md


def test_html_to_markdown_handles_tables_and_lists():
    md = html_to_markdown(FIXTURE)
    # 表格转 markdown 后通常保留 | 分隔
    assert "str" in md
    # 列表保留 -
    assert "第一项" in md


def test_extract_links_finds_anchors():
    md = html_to_markdown(FIXTURE)
    links = extract_links(md)
    # 应该包含 fixture 里有的两个完整 URL
    urls = set(links)
    assert any("python.org" in u for u in urls), f"expected python.org link, got {links}"


def test_extract_links_dedups():
    md = "[a](https://x.com/1) and [b](https://x.com/1) and [c](https://x.com/2)"
    links = extract_links(md)
    assert len(links) == 2
    assert "https://x.com/1" in links
    assert "https://x.com/2" in links


if __name__ == "__main__":
    test_html_to_markdown_removes_scripts_and_styles()
    test_html_to_markdown_extracts_main_content()
    test_html_to_markdown_handles_tables_and_lists()
    test_extract_links_finds_anchors()
    test_extract_links_dedups()
    print("✓ fetch tests passed")
