@echo off
chcp 65001 >nul
cd /d "%~dp0"
call :main > install_log.txt 2>&1
start notepad "%~dp0install_log.txt"
exit /b

:findpy
set PY313=
for %%P in ("%LOCALAPPDATA%\Programs\Python\Python313\python.exe" "%LOCALAPPDATA%\Python\pythoncore-3.13-64\python.exe" "C:\Program Files\Python313\python.exe") do (
  if not defined PY313 if exist "%%~P" set "PY313=%%~P"
)
if not defined PY313 (
  where py >nul 2>nul && for /f "delims=" %%i in ('py -3.13 -c "import sys;print(sys.executable)" 2^>nul') do set "PY313=%%i"
)
exit /b

:main
set PYTHONIOENCODING=utf-8
echo === existing python ===
where python
where py
echo === python 3.13 ===
call :findpy
if not defined PY313 (
  where py >nul 2>nul && py install -y 3.13
  call :findpy
)
if not defined PY313 (
  echo installing Python 3.13 with winget...
  winget install --id Python.Python.3.13 -e --scope user --silent --accept-package-agreements --accept-source-agreements
  call :findpy
)
if not defined PY313 (
  echo [FAIL] Python 3.13 not found
  exit /b 1
)
echo %PY313%
echo === pip install mcp ===
"%PY313%" -m pip install --upgrade --quiet pip mcp
"%PY313%" -c "import mcp; print('mcp ok')"
echo === claude app ===
powershell -NoProfile -Command "Get-Process claude -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Path -Unique"
dir "%LOCALAPPDATA%\Claude-Data" /b
echo === claude config ===
"%PY313%" merge_config.py "%PY313%"
echo === server self-test ===
echo {"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"setup","version":"1"}}}| "%PY313%" rhino_mcp_server.py
echo.
echo === rhino connection test (needs listener running in Rhino) ===
"%PY313%" test_connection.py
echo === done ===
exit /b
