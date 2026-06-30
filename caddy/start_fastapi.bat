@echo off
chcp 65001 >nul
title 青橙 FastAPI 后端

:: 由 Start_Caddy.bat 调用
:: 切到 backend/ 目录启动 uvicorn

cd /d "%~dp0"
cd ..\backend

:: 便携 Python 路径：qc-pocket-space/launcher/python/python.exe
set "PORTABLE_PYTHON=%~dp0..\launcher\python\python.exe"

if exist "%PORTABLE_PYTHON%" (
    echo [FastAPI] 使用便携 Python 启动 (端口 8000, 热重载)...
    "%PORTABLE_PYTHON%" -m uvicorn main:app --reload --port 8000
) else (
    echo ========================================
    echo  [错误] 找不到便携 Python！
    echo  路径: %PORTABLE_PYTHON%
    echo  请确认 launcher/python/ 已正确安装
    echo ========================================
    pause
    exit /b 1
)

echo.
echo [FastAPI] 进程已退出，按任意键关闭窗口...
pause
