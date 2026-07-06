# web-mcp

本地给 Claude 用的网络访问 MCP server。

Claude 自带 `WebSearch` / `WebFetch`，但没法配代理、改 UA、渲染 JS、把图片落到磁盘。这个 MCP 就是把这些可控起来：**代码在自己手里，要怎么抓自己说了算**。

## 4 个工具

| 工具 | 用途 | 后端 |
|---|---|---|
| `web_search` | DuckDuckGo 文本搜索 | `ddgs` (无需 Key) |
| `fetch_url` | 抓取 URL 转 markdown | `httpx` + `trafilatura` (静态) / `Playwright` (render=true) |
| `download_image` | 下载图片到本地 | `httpx` + `Pillow` |
| `search_and_fetch` | 搜 + 一次性取前 N 页 markdown | 组合上述 |

> **图像理解**：故意不在 MCP 里。让 `download_image` 返回路径，Claude 直接 `Read(path)` 走原生视觉通道。

## 安装 (Windows 11)

需要 Python 3.10+。打开 PowerShell：

```powershell
cd D:\桌面\web-mcp
.\install.bat
```

自动做：
1. 创建 `.venv`
2. 升级 pip
3. 装所有 Python 包（`mcp`, `httpx`, `ddgs`, `playwright`, ...）
4. 下载 Playwright Chromium（~150MB）
5. 复制 `.env.example` → `.env`

## 配置 (可选)

编辑 `.env`：

```bash
# 走 verge-mihomo 代理
WEB_MCP_PROXY=http://127.0.0.1:7890

# 改 DDG region
WEB_MCP_DDG_REGION=cn-zh

# 改图片大小上限
WEB_MCP_MAX_IMAGE_SIZE_MB=20
```

## 接入 Claude Code

打开 `C:\Users\LENOVO\.claude.json`（如果用 Claude Code 全局配置），合并 `mcpServers`：

```json
{
  "mcpServers": {
    "web": {
      "command": "D:\\桌面\\web-mcp\\.venv\\Scripts\\python.exe",
      "args": ["-m", "web_mcp"],
      "cwd": "D:\\桌面\\web-mcp",
      "env": {
        "PYTHONUNBUFFERED": "1",
        "PYTHONIOENCODING": "utf-8"
      }
    }
  }
}
```

**注意**：
- `PYTHONUNBUFFERED=1` + `PYTHONIOENCODING=utf-8` **必须**，否则 Windows stdio JSON-RPC 流会被中文 URL 和进度打印破坏
- venv Python 要用**绝对路径** `.venv\Scripts\python.exe`
- 改完 MCP 配置**必须重启** Claude Code（不热加载）

如果是 Claude Desktop，配置文件在 `%APPDATA%\Claude\claude_desktop_config.json`，格式同上。

## 测试用例

启动 Claude Code 后让它执行：

| 测试 | 提示词 | 期望结果 |
|---|---|---|
| 搜索 | "用 web 搜索 Python MCP tutorial" | ≥3 条带 href 的结果 |
| 静态抓取 | "用 web fetch https://zh.wikipedia.org/wiki/Python" | `rendered: false`，markdown >500 字 |
| 渲染抓取 | "用 web fetch https://news.ycombinator.com/ 用 render=true" | `rendered: true`，内容比静态长 |
| 图片下载 | "用 web 下载 https://www.python.org/static/img/python-logo.png 然后告诉我图上画了什么" | 返回 path；接着被 Read 看图 |
| 组合 | "用 web search_and_fetch 'Python 3.13 release notes'" | 一次拿到 query + 多页 pages |

## 调试

**单独测试 MCP server**（不通过 Claude）：

```powershell
.\start.bat
```

应该 1-2 秒后无输出地挂起等 stdin。按 Ctrl+C 干净退出。

**看日志**：stdio 模式下日志走 stderr，可以在 `start.bat` 后面加 `2> mcp.log` 看到 `logging` 输出。

**Claude Desktop 日志**：`%APPDATA%\Claude\logs\mcp-*.log`

## 已知限制

- **DDG 限流**：连续 50+ 次搜索触发 `RatelimitException`。代码已加重试 3 次 + 指数退避，但仍可能拿到空列表。
- **`x.com` / `linkedin.com` / `facebook.com`**：需要登录态，普通 HTTP 和 Playwright 都拿不到正文。
- **>10MB 图片直接拒**（按 `WEB_MCP_MAX_IMAGE_SIZE_MB`）。
- **大文件下载**：流式限制在 `WEB_MCP_MAX_IMAGE_SIZE_MB`。

## 目录结构

```
D:\桌面\web-mcp\
├── .env.example             # 配置模板
├── README.md                # 本文件
├── install.bat              # 一键安装
├── start.bat                # 手动测试 MCP server
├── requirements.txt
├── pyproject.toml
├── src\web_mcp\
│   ├── server.py            # FastMCP + lifespan + 4 个工具
│   ├── search.py            # ddgs 异步搜索
│   ├── fetch.py             # httpx + trafilatura + markdownify
│   ├── browser.py           # Playwright (lifespan 管理)
│   ├── images.py            # httpx + Pillow 校验 + 哈希去重
│   ├── config.py            # pydantic-settings
│   └── utils.py             # smart_truncate
├── tests\
│   └── ...                  # 离线单元测试
└── downloads\               # 默认图片保存目录
```

## 后续可加 (v0.2+)

- 缓存层 (`diskcache`) - 同 URL 短时间内不重复抓
- PDF 抓取 (`pypdf` + `pdfminer.six`)
- YouTube 字幕 (`youtube-transcript-api`)
- 网页截图 (`screenshot_url`)
- Cookie 持久化 (复用登录态)

## License

MIT
