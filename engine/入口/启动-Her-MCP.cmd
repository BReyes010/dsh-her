@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
rem ============================================================
rem  Her MCP server (read-only) - local port 8790
rem  Close this window to stop.
rem  ASCII-only on purpose. Python is auto-detected:
rem    1) DSH_PY env var
rem    2) python on PATH
rem    3) any python.exe under %USERPROFILE%\.dsh\dsh-runtimes
rem ============================================================
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
set "HERE=%~dp0"
set "PY=%DSH_PY%"

if not defined PY (
  for /f "delims=" %%i in ('where python 2^>nul') do (
    if not defined PY set "PY=%%i"
  )
)
if not defined PY (
  for /f "delims=" %%i in ('dir /b /s "%USERPROFILE%\.dsh\dsh-runtimes\python.exe" 2^>nul') do (
    if not defined PY set "PY=%%i"
  )
)
if not defined PY (
  echo [X] Cannot find python.exe. Install Python 3.11+ or set DSH_PY.
  pause
  exit /b 1
)

echo ============================================================
echo   Her MCP server (read-only)   http://127.0.0.1:8790
echo   python: %PY%
echo   Close this window to stop.
echo ============================================================
"%PY%" "%HERE%her-mcp-http.py" --port 8790 --print-url
pause
endlocal
