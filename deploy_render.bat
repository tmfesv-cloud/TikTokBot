@echo off
chcp 65001 >nul
title Deploy TikTokBot -> Render
cd /d "%~dp0"

:: Пути
set "SRC=%CD%"
set "REPO=C:\Users\times\Documents\GitHub\TikTokBot"
set "GIT=C:\Program Files\Git\cmd\git.exe"

echo ==============================================
echo    Деплой TikTokBot на Render
echo ==============================================
echo.

if not exist "%GIT%" (
    echo [ОШИБКА] Git не найден: %GIT%
    pause
    exit /b 1
)
if not exist "%REPO%\.git" (
    echo [ОШИБКА] Репозиторий не найден: %REPO%
    echo Создай его на GitHub и склонируй, затем повтори.
    pause
    exit /b 1
)

:: 1. Копируем файлы в репозиторий (без .env, .venv, downloads)
echo [1/4] Копирование файлов в репозиторий...
xcopy /y /e /i /q "%SRC%\bot.py"            "%REPO%\" >nul
xcopy /y /e /i /q "%SRC%\config.py"         "%REPO%\" >nul
xcopy /y /e /i /q "%SRC%\requirements.txt"  "%REPO%\" >nul
xcopy /y /e /i /q "%SRC%\Dockerfile"        "%REPO%\" >nul
xcopy /y /e /i /q "%SRC%\Procfile"          "%REPO%\" >nul
xcopy /y /e /i /q "%SRC%\runtime.txt"       "%REPO%\" >nul
xcopy /y /e /i /q "%SRC%\README.md"         "%REPO%\" >nul
xcopy /y /e /i /q "%SRC%\app"               "%REPO%\app\" >nul
xcopy /y /e /i /q "%SRC%\downloads"         "%REPO%\downloads\" >nul
xcopy /y /e /i /q "%SRC%\.env.example"      "%REPO%\" >nul
echo    Файлы скопированы.

:: 2. Коммитим
echo [2/4] Коммит изменений...
cd /d "%REPO%"
"%GIT%" add -A
"%GIT%" commit -m "update: deploy %date% %time%" >nul 2>&1
echo    Коммит создан.

:: 3. Пушим на GitHub
echo [3/4] Пуш на GitHub...
"%GIT%" push origin main
if errorlevel 1 (
    echo.
    echo [ВНИМАНИЕ] Пуш не удался. Возможно, нужно войти в аккаунт.
    echo Открой GitHub Desktop и нажми "Push origin" вручную.
) else (
    echo    Код запушен!
)

:: 4. Пересборка на Render
echo [4/4] Пересборка на Render...
set "HOOK_FILE=%SRC%\render_hook.txt"
if exist "%HOOK_FILE%" (
    set /p HOOK_URL=<"%HOOK_FILE%"
    curl -s -o nul -X POST "%HOOK_URL%"
    echo    Деплой запущен через deploy-hook!
) else (
    echo    Файл render_hook.txt не найден.
    echo    Зайди на Render и нажми "Manual Deploy" вручную.
    echo    Или создай Deploy Hook в Render (Settings - Deploy Hook),
    echo    сохрани его URL в файл render_hook.txt рядом с этим батником.
)

echo.
echo Готово!
pause
