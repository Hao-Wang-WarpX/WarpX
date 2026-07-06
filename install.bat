@echo off
REM ============================================================
REM web-mcp Windows one-shot installer
REM Steps: create venv -> upgrade pip -> install Python deps -> install Chromium
REM ============================================================
setlocal enabledelayedexpansion

set "ROOT=%~dp0"
cd /d "%ROOT%"

REM Check py launcher
where py >nul 2>nul
if errorlevel 1 (
    echo [ERROR] py launcher not found. Install Python 3.10+ first.
    echo         Download: https://www.python.org/downloads/
    exit /b 1
)

echo.
echo === web-mcp installer ===
echo ROOT: %ROOT%
echo.

REM 1. Create venv
REM Use Python 3.11 (has prebuilt wheels for lxml / playwright on Windows).
if not exist ".venv\Scripts\python.exe" (
    echo [1/5] Creating venv with Python 3.11 ...
    py -3.11 -m venv .venv
    if errorlevel 1 (
        echo [WARN]  Python 3.11 not available, falling back to default ...
        py -3 -m venv .venv
        if errorlevel 1 (
            echo [ERROR] Failed to create venv
            exit /b 1
        )
    )
) else (
    echo [1/5] venv exists, skipping
)

REM 2. Upgrade pip
echo [2/5] Upgrading pip ...
call .venv\Scripts\python.exe -m pip install --upgrade pip --quiet
if errorlevel 1 (
    echo [ERROR] Failed to upgrade pip
    exit /b 1
)

REM 3. Install deps
echo [3/5] Installing Python dependencies - this may take 1-2 minutes ...
call .venv\Scripts\python.exe -m pip install -e ".[dev]"
if errorlevel 1 (
    echo [ERROR] Failed to install dependencies
    exit /b 1
)

REM 4. Install Chromium - skip for now, do manually later to avoid hanging on slow CDN
echo [4/5] SKIPPING Playwright Chromium install - run manually later if needed:
echo      .venv\Scripts\python.exe -m playwright install chromium
echo      (render=true needs it; search/fetch/download work without it)

REM 5. Copy .env.example -^> .env if missing
if not exist ".env" (
    echo [5/5] Copying .env.example to .env ...
    copy /Y ".env.example" ".env" >nul
) else (
    echo [5/5] .env exists, skipping
)

echo.
echo ============================================================
echo [OK] Installation complete!
echo ============================================================
echo.
echo Next steps:
echo   1. Optional - edit .env to set WEB_MCP_PROXY=http://127.0.0.1:7890
echo   2. Configure Claude Code/Desktop - see README.md
echo   3. Test MCP server standalone - run start.bat
echo.
exit /b 0