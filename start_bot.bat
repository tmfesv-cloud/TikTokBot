@echo off
chcp 65001 >nul
title TikTokBot - запуск
cd /d "%~dp0"

echo ==============================
echo    TikTokBot - запуск
echo ==============================
echo.

:: Проверяем наличие Python (обычный, либо launcher `py`)
set "PYTHON="
where python >nul 2>&1
if errorlevel 1 (
    where py >nul 2>&1
    if errorlevel 1 (
        echo [ОШИБКА] Python не найден в PATH.
        echo Установи Python с https://www.python.org/downloads/
        echo и поставь галочку "Add to PATH" при установке.
        pause
        exit /b 1
    )
    set "PYTHON=py"
) else (
    set "PYTHON=python"
)

:: Проверяем, что Python реально запускается (не Store-заглушка)
%PYTHON% --version >nul 2>&1
if errorlevel 1 (
    echo [ОШИБКА] "%PYTHON%" не запускается. Установи Python с python.org.
    pause
    exit /b 1
)

:: Создаём виртуальное окружение если его нет
if not exist ".venv\Scripts\python.exe" (
    echo [1/3] Создание виртуального окружения...
    %PYTHON% -m venv .venv
    if errorlevel 1 (
        echo [ОШИБКА] Не удалось создать venv.
        pause
        exit /b 1
    )
) else (
    echo [1/3] Виртуальное окружение уже есть.
)

:: Устанавливаем зависимости
echo [2/3] Установка зависимостей...
".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
".venv\Scripts\python.exe" -m pip install --quiet -r requirements.txt
if errorlevel 1 (
    echo [ВНИМАНИЕ] Ошибка при установке зависимостей.
)

:: Запускаем бота
echo [3/3] Запуск бота...
echo.
echo Бот работает. Для остановки нажми Ctrl+C
echo.
".venv\Scripts\python.exe" bot.py

echo.
echo Бот остановлен.
pause
