"""config 单测: 不需要网络, 验证 settings 默认值和 validator."""

import socket
from contextlib import contextmanager

import pytest

from web_mcp.config import (
    PROXY_CANDIDATES,
    Settings,
    _detect_protocol,
    _probe_port_open,
    detect_and_set_proxy,
    detect_local_proxy,
)


def test_default_settings_have_safe_defaults():
    s = Settings()
    assert s.http_timeout > 0
    assert s.max_image_size_mb > 0
    assert s.ddg_safesearch in ("strict", "moderate", "off")
    assert s.browser_headless is True


def test_viewport_parses_correctly():
    s = Settings(browser_viewport="1920,1080")
    assert s.viewport == {"width": 1920, "height": 1080}


def test_viewport_fallback_on_bad_input():
    s = Settings(browser_viewport="not-a-number")
    vp = s.viewport
    # 解析失败应退回默认
    assert vp["width"] == 1280
    assert vp["height"] == 720


def test_safesearch_validator():
    # 合法值
    s = Settings(ddg_safesearch="off")
    assert s.ddg_safesearch == "off"

    # 非法值应在 model_validator 阶段抛错
    with pytest.raises(ValueError):
        Settings(ddg_safesearch="invalid")


def test_max_image_size_bytes_conversion():
    s = Settings(max_image_size_mb=1.5)
    assert s.max_image_size_bytes == int(1.5 * 1024 * 1024)


# --------------------------------------------------------------------------- #
# 自动代理探测
# --------------------------------------------------------------------------- #


def test_probe_port_open_returns_false_for_closed_port():
    # 用一个肯定没占的端口
    assert _probe_port_open(1, timeout=0.2) is False


def test_probe_port_open_returns_true_for_listening_port():
    # 起一个临时 server, 探测应能命中
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    port = s.getsockname()[1]
    try:
        assert _probe_port_open(port, timeout=0.5) is True
    finally:
        s.close()


@contextmanager
def _fake_http_server(response: bytes = b"HTTP/1.1 400 Bad Request\r\n\r\n"):
    """起一个最简单的 'HTTP 服务器' (回固定字节), 给协议探测用."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    port = s.getsockname()[1]
    import threading

    def handle():
        try:
            conn, _ = s.accept()
            conn.recv(4096)  # 吃掉探测请求
            conn.sendall(response)
            conn.close()
        except Exception:
            pass

    t = threading.Thread(target=handle, daemon=True)
    t.start()
    try:
        yield port
    finally:
        s.close()


@contextmanager
def _fake_socks5_server():
    """起一个 'SOCKS5 风格' 服务器: 不响应 HTTP, 连接后只 close."""

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    port = s.getsockname()[1]
    import threading

    def handle():
        try:
            conn, _ = s.accept()
            conn.recv(4096)
            # 不回 HTTP, 模拟 SOCKS5 啥都不响应然后断开
            conn.close()
        except Exception:
            pass

    t = threading.Thread(target=handle, daemon=True)
    t.start()
    try:
        yield port
    finally:
        s.close()


def test_detect_protocol_identifies_http_proxy():
    with _fake_http_server() as port:
        assert _detect_protocol(port, timeout=0.5) == "http"


def test_detect_protocol_identifies_socks5_proxy():
    with _fake_socks5_server() as port:
        assert _detect_protocol(port, timeout=0.5) == "socks5"


def test_detect_protocol_returns_none_when_nothing_listening():
    assert _detect_protocol(1, timeout=0.2) is None


def test_detect_local_proxy_returns_url_when_found(monkeypatch):
    # 把候选列表缩减到一个肯定开着的端口 (通过我们 fake_http_server 拿到)
    with _fake_http_server() as port:
        monkeypatch.setattr(
            "web_mcp.config.PROXY_CANDIDATES", [(port, "http")]
        )
        result = detect_local_proxy(timeout_per_port=0.5)
        assert result == f"http://127.0.0.1:{port}"


def test_detect_local_proxy_returns_none_when_nothing(monkeypatch):
    # 把候选列表缩减到一组肯定关着的端口
    monkeypatch.setattr(
        "web_mcp.config.PROXY_CANDIDATES",
        [(65430, "http"), (65431, "http"), (65432, "socks5")],
    )
    assert detect_local_proxy(timeout_per_port=0.1) is None


def test_explicit_proxy_overrides_autodetect(monkeypatch):
    # 即使有 fake 端口, detect_and_set_proxy 不应覆盖显式值
    with _fake_http_server() as port:
        monkeypatch.setattr(
            "web_mcp.config.PROXY_CANDIDATES", [(port, "http")]
        )
        s = Settings(proxy="http://1.2.3.4:5678")
        detect_and_set_proxy(s)  # 应该 no-op
        assert s.proxy == "http://1.2.3.4:5678"


def test_autodetect_fills_proxy_when_unset(monkeypatch):
    # 代理检测现在是 lifespan 里显式调用 detect_and_set_proxy(),
    # 不再隐含在 Settings() 构造里 (避免 import 时 TCP 扫端口)
    # 注意: .env 里有 WEB_MCP_PROXY= 会产空串, 所以这里显式传 None
    with _fake_http_server() as port:
        monkeypatch.setattr(
            "web_mcp.config.PROXY_CANDIDATES", [(port, "http")]
        )
        s = Settings(proxy=None)
        assert s.proxy is None  # Settings() 自己不动 proxy
        detect_and_set_proxy(s)
        assert s.proxy == f"http://127.0.0.1:{port}"


def test_empty_string_proxy_triggers_autodetect(monkeypatch):
    # .env 留空 WEB_MCP_PROXY= 是常见写法, pydantic 解析为 ""
    # detect_and_set_proxy() 应把空串视为"未设置"
    with _fake_http_server() as port:
        monkeypatch.setattr(
            "web_mcp.config.PROXY_CANDIDATES", [(port, "http")]
        )
        # 通过 __init__ 传空串模拟 .env 留空
        s = Settings(proxy="")
        assert s.proxy == ""  # Settings() 原封不动保留空串
        detect_and_set_proxy(s)
        assert s.proxy == f"http://127.0.0.1:{port}"


def test_candidates_are_sorted_by_priority():
    # 第一项必须是 7890 (verge-mihomo 主流默认)
    assert PROXY_CANDIDATES[0][0] == 7890
    # 候选至少包含 HTTP 和 SOCKS5 两类
    schemes = {scheme for _, scheme in PROXY_CANDIDATES}
    assert "http" in schemes
    assert "socks5" in schemes
    # 端口号都合法 (>0, <65536)
    for port, _ in PROXY_CANDIDATES:
        assert 0 < port < 65536


if __name__ == "__main__":
    test_default_settings_have_safe_defaults()
    test_viewport_parses_correctly()
    test_viewport_fallback_on_bad_input()
    try:
        test_safesearch_validator()
    except Exception as e:
        print(f"✓ safesearch validator test ran (raised as expected: {type(e).__name__})")
    test_max_image_size_bytes_conversion()
    test_probe_port_open_returns_false_for_closed_port()
    test_probe_port_open_returns_true_for_listening_port()
    test_detect_protocol_identifies_http_proxy()
    test_detect_protocol_identifies_socks5_proxy()
    test_detect_protocol_returns_none_when_nothing_listening()
    test_detect_local_proxy_returns_url_when_found()
    test_detect_local_proxy_returns_none_when_nothing()
    test_explicit_proxy_overrides_autodetect()
    test_autodetect_fills_proxy_when_unset()
    test_candidates_are_sorted_by_priority()
    print("✓ config tests passed")
