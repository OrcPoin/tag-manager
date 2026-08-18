@echo off
setlocal
set "APP_DIR=%~dp0"
if not exist "%APP_DIR%tag_manager.pyw" (
    echo Tag Manager launcher not found.
    pause
    exit /b 1
)

rem На всякий случай: если интерфейс ещё не собран (первый запуск, чистый clone),
rem собираем его один раз. Дальше запуск идёт мгновенно, без консоли.
if not exist "%APP_DIR%frontend\dist\index.html" (
    echo Интерфейс ещё не собран — собираю в первый раз, это займёт минуту...
    call "%APP_DIR%build-frontend.bat"
    if errorlevel 1 (
        echo.
        echo [!] Не удалось собрать интерфейс. Установите Node.js 20+ и запустите снова.
        pause
        exit /b 1
    )
)

start "" /b pythonw.exe "%APP_DIR%tag_manager.pyw"
exit /b 0
