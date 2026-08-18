@echo off
setlocal
set "APP_DIR=%~dp0"
if exist "%APP_DIR%tag_manager.pyw" (
    start "" /b pythonw.exe "%APP_DIR%tag_manager.pyw"
    exit /b 0
)
echo Tag Manager launcher not found.
pause
exit /b 1
