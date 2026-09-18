@echo off
rem gha.cmd — gha-runner 启动器（Windows）。Unix/macOS 用同目录的 gha。
rem
rem 注意：本文件必须是 CRLF 行尾。cmd.exe 对 LF-only 的批处理在 goto/label
rem 上有已知的解析问题，而这里用到了 label。

setlocal
set "PYTHONPATH=%~dp0."

where py >nul 2>nul
if not errorlevel 1 goto py

where python >nul 2>nul
if not errorlevel 1 goto python

where python3 >nul 2>nul
if not errorlevel 1 goto python3

echo gha: 需要 Python ^>= 3.9，但找不到 py / python / python3 1>&2
echo      winget install Python.Python.3.12  或  https://www.python.org/downloads/ 1>&2
exit /b 1

:py
py -3 -m gha_runner %*
exit /b %errorlevel%

:python
python -m gha_runner %*
exit /b %errorlevel%

:python3
python3 -m gha_runner %*
exit /b %errorlevel%
