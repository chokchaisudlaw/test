@echo off
chcp 65001 >nul
cd /d "%~dp0"

REM เปิดหน้าต่างโปรแกรมโดยไม่ค้างหน้าต่าง CMD (ถ้ามี pythonw)
where pythonw >nul 2>&1
if %errorlevel%==0 (
  pythonw gui.py
  exit /b %errorlevel%
)

python gui.py
if errorlevel 1 pause
