@echo off
:: 设置控制台为 UTF-8 编码，防止中文乱码
chcp 65001 >nul
title 青橙 Caddy 统一环境启动向导

:: ========================================================
:: 第一步：动态计算项目根目录（保留 Windows 反斜杠）
:: ========================================================
set "CADDY_DIR=%~dp0"
cd /d "%CADDY_DIR%"
cd ..
set "WEBSITE_ROOT=%cd%"
cd /d "%CADDY_DIR%"

echo ========================================
echo          青橙 Studio 智能环境向导
echo ========================================
echo   [项目根目录]: %WEBSITE_ROOT%
echo ========================================
echo  [1] 本地测试模式 (localhost)
echo  [2] 线上生产模式 (codenova.cn)
echo ========================================
echo.

set /p choice="请输入对应数字 (1 或 2) 并按回车: "

if "%choice%"=="1" goto mode_local
if "%choice%"=="2" goto mode_server

echo.
echo [错误] 无效的输入，请重新运行。
pause
exit

:: ========================================================
:mode_local
:: ========================================================
echo.
echo =^> [1/3] Python 生成 Caddyfile（注入绝对路径）...
python "%CADDY_DIR%generate_caddyfile.py" local
if errorlevel 1 (
    echo [错误] Caddyfile 生成失败！请确认 Python 已安装并加入 PATH。
    pause
    exit
)

echo.
echo =^> [2/3] 启动 FastAPI 后端（新窗口，带热重载）...
start "青橙 FastAPI" "%CADDY_DIR%start_fastapi.bat"

echo =^> [3/3] 清理旧进程并启动 Caddy...
taskkill /f /im caddy_windows_amd64_custom.exe >nul 2>&1
caddy_windows_amd64_custom.exe run
pause
exit

:: ========================================================
:mode_server
:: ========================================================
echo.
echo =^> [1/3] Python 生成 Caddyfile（注入绝对路径 + 线上域名）...
python "%CADDY_DIR%generate_caddyfile.py" server
if errorlevel 1 (
    echo [错误] Caddyfile 生成失败！请确认 Python 已安装并加入 PATH。
    pause
    exit
)

echo.
echo =^> [2/3] 启动 FastAPI 后端（新窗口，带热重载）...
start "青橙 FastAPI" "%CADDY_DIR%start_fastapi.bat"

echo =^> [3/3] 清理旧进程并启动 Caddy（Let's Encrypt HTTPS）...
taskkill /f /im caddy_windows_amd64_custom.exe >nul 2>&1
caddy_windows_amd64_custom.exe run
pause
exit