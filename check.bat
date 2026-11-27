@echo off
chcp 65001 >nul
cd /d "%~dp0"
call :main > check_log.txt 2>&1
exit /b

:main
set CFG=%LOCALAPPDATA%\Claude-Data\claude_pzs8sxrjxfjjc\claude_desktop_config.json
echo === config ===
type "%CFG%"
echo.
echo === claude start times ===
powershell -NoProfile -Command "Get-Process claude -ErrorAction SilentlyContinue | Sort-Object StartTime | Select-Object -First 3 Id,StartTime | Format-Table -AutoSize | Out-String"
echo === python processes ===
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Select-Object ProcessId,CommandLine | Format-List | Out-String -Width 300"
echo === logs ===
dir "%LOCALAPPDATA%\Claude\logs" /o-d /b
for %%F in (mcp.log mcp-server-rhino7.log) do (
  if exist "%LOCALAPPDATA%\Claude\logs\%%F" (
    echo ===== %%F =====
    powershell -NoProfile -Command "Get-Content -Tail 40 -Encoding UTF8 '%LOCALAPPDATA%\Claude\logs\%%F'"
  )
)
exit /b
