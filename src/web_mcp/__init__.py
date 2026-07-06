"""web-mcp: 本地 web 访问 MCP server.

暴露 4 个工具给 Claude:
- web_search
- fetch_url (含可选 JS 渲染)
- download_image
- search_and_fetch

图像理解故意不在此 MCP 内: Claude 原生 Read 工具支持 PNG/JPG/WebP,
download_image 返回路径后用 Read(path) 即可走视觉通道.
"""

__version__ = "0.1.0"
