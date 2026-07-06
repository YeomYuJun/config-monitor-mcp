@echo off
REM claude-snap.bat - cmd 용 래퍼. 예) claude-snap status / claude-snap snapshot -m "msg" / claude-snap config
setlocal
set DIR=%~dp0
set SRC=%DIR%..\src\
if "%~1"=="config" (
  python "%SRC%claude_config.py" report -o "%DIR%claude-status.html"
  start "" "%DIR%claude-status.html"
) else (
  python "%SRC%cas.py" %*
)
endlocal
