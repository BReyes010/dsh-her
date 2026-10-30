@echo off
setlocal
rem Remove HerMCP from startup and stop the running server.
set "LNK=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\HerMCP.lnk"
if exist "%LNK%" del /f /q "%LNK%" && echo Removed: %LNK%
echo.
echo Stopping python processes that run her-mcp-http.py ...
for /f "tokens=2 delims=," %%p in ('tasklist /fi "imagename eq python.exe" /fo csv /nh 2^>nul') do (
  wmic process where "ProcessId=%%~p" call terminate >nul 2>&1
)
echo Done. Autostart removed.
pause
