@echo off
title Boss直聘 AI 求职助手
cd /d "%~dp0"

echo ============================================
echo    Boss直聘 AI 求职助手
echo ============================================
echo.
echo   [1] 采样模式    抓 5 个职位卡片样本（做字段提取用）
echo   [2] 演练模式    完整 AI 评估，但不发送任何消息
echo   [3] 演练模式x10  同上，只看前 10 个职位（推荐先用这个）
echo   [4] 正式运行    自动打招呼（真实发送！）
echo.
set /p choice=请选择 [1-4]，直接回车=退出:

if "%choice%"=="1" goto :mode_probe
if "%choice%"=="2" goto :mode_dry
if "%choice%"=="3" goto :mode_dry10
if "%choice%"=="4" goto :confirm_run
echo 已退出（未选择任何模式）
pause
goto :eof

:mode_probe
set ARGS=--probe 5
goto :check_env

:mode_dry
set ARGS=--dry-run
goto :check_env

:mode_dry10
set ARGS=--dry-run --max-jobs 10
goto :check_env

:confirm_run
echo.
set /p confirm=正式运行会真实发送打招呼消息，确认继续? (y/n):
if /i not "%confirm%"=="y" (
    echo 已取消
    pause
    goto :eof
)
set ARGS=
goto :check_env

:check_env
if exist ".env" goto :run
echo.
echo [警告] 未找到 .env 文件，AI 评估/话术功能将不可用！
echo        请把 .env.example 复制一份改名为 .env，填入 AI_API_KEY
echo.
set /p go=仍要继续吗? (y/n):
if /i not "%go%"=="y" (
    echo 已退出
    pause
    goto :eof
)

:run
echo.
".venv\Scripts\python.exe" main.py %ARGS%
echo.
echo ============================================
echo  运行结束。日志在 logs\ 目录，投递记录在 data\boss.db
echo ============================================
pause
