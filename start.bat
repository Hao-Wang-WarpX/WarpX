@echo off
REM ============================================================
REM Manually start web-mcp MCP server (stdio mode)
REM Use this to test the MCP server standalone
REM Ctrl+C to exit
REM ============================================================
setlocal

set "ROOT=%~dp0"
cd /d "%ROOT%"

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] venv not found. Run install.bat first.
    exit /b 1
)

echo Starting web-mcp. Press Ctrl+C to exit...
.\.venv\Scripts\python.exe -m web_mcp