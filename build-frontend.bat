@echo off
chcp 65001 >nul
rem Сборка веб-интерфейса Tag Manager (React + Vite -> frontend\dist).
rem Вызывается из "Start Tag Manager.bat" (первый запуск) и update.bat (после git pull).
rem Можно запустить и вручную, если нужно пересобрать интерфейс.
setlocal

where npm >nul 2>nul
if errorlevel 1 (
    echo [!] Node.js/npm не найден. Установите Node.js 20+ с https://nodejs.org
    exit /b 1
)

pushd "%~dp0frontend"
if errorlevel 1 (
    echo [!] Папка frontend не найдена рядом со скриптом.
    exit /b 1
)

echo [1/2] Устанавливаю зависимости фронтенда...
if exist "package-lock.json" (
    call npm ci
) else (
    call npm install
)
if errorlevel 1 (
    echo [!] Установка npm-зависимостей не удалась.
    popd
    exit /b 1
)

echo [2/2] Собираю интерфейс (npm run build)...
call npm run build
if errorlevel 1 (
    echo [!] npm run build не прошёл.
    popd
    exit /b 1
)

popd
echo [ok] Интерфейс собран: frontend\dist
exit /b 0
