@echo off
rem ============================================
rem  Boss直聘 AI 求职助手 - CLI 入口（任意目录可用）
rem  自主模式（旧用法兼容）:
rem    boss.bat --dry-run            演练（不发消息）
rem    boss.bat --max-greets 2       正式，发满2条打招呼即停
rem  原子命令（agent/skill 用法）:
rem    boss.bat status               查看登录/配额/配置
rem    boss.bat jobs                 当前列表
rem    boss.bat detail 职位ID        抓职位描述
rem    boss.bat greet 职位ID --msg "话术"
rem    boss.bat inbox                未读消息
rem    boss.bat archive --top 20     职位档案
rem  交互菜单请双击 start.bat
rem ============================================
cd /d "%~dp0"
".venv\Scripts\python.exe" "%~dp0cli.py" %*
if errorlevel 1 pause
