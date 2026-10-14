@echo off
setlocal
cd /d "%~dp0"
set "PYCMD="
where py >nul 2>nul && set "PYCMD=py -3"
if not defined PYCMD where python >nul 2>nul && set "PYCMD=python"
if not defined PYCMD (
    echo [GUY] Python 3 nao encontrado.
    echo        Instale em https://www.python.org/downloads/ e marque "Add python.exe to PATH".
    pause
    exit /b 1
)
%PYCMD% main.py %*
if errorlevel 1 (
    echo.
    echo [GUY] O programa terminou com erro. Veja a mensagem acima.
    pause
)
endlocal
