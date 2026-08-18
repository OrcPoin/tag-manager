@echo off
chcp 65001 >nul
rem Обновление Tag Manager до свежей версии. Двойной клик — и готово,
rem в консоль лезть не нужно. Тянет изменения из GitHub и доставляет зависимости.
setlocal
cd /d "%~dp0"

where git >nul 2>nul
if errorlevel 1 (
    echo [!] Git не найден. Установите Git с https://git-scm.com
    echo     или скачайте свежий zip со страницы релизов.
    goto :end
)

if not exist ".git" (
    echo [!] Это не git-копия ^(нет папки .git^) — обновиться через git нельзя.
    echo     Похоже, вы скачали zip. Чтобы обновляться в один клик, один раз
    echo     склонируйте репозиторий:
    echo         git clone https://github.com/OrcPoin/tag-manager.git
    goto :end
)

echo === Обновление Tag Manager ===
echo.
echo [1/3] Тяну изменения из GitHub...
git pull
if errorlevel 1 (
    echo.
    echo [!] git pull не прошёл. Частая причина — локальные правки в файлах.
    echo     Сохраните их или откатите, затем запустите обновление снова.
    goto :end
)

echo.
echo [2/3] Обновляю зависимости Python...
python -m pip install -r requirements.txt

echo.
echo [3/3] Пересобираю интерфейс...
call "%~dp0build-frontend.bat"
if errorlevel 1 (
    echo.
    echo [!] Интерфейс не пересобрался. Установите Node.js 20+ и запустите обновление снова.
    goto :end
)

echo.
echo === Готово. Запускайте "Start Tag Manager.bat" ===

:end
echo.
pause
endlocal
