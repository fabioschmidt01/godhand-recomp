@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" call :setup || exit /b 1
start "" ".venv\Scripts\pythonw.exe" -m recomp gui
exit /b 0

:setup
echo Preparando o ambiente Python (so na primeira execucao)...
python -m venv .venv
if errorlevel 1 (
    echo Python 3.11 ou mais novo nao foi encontrado. Instale em https://www.python.org/downloads/ e rode de novo.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
exit /b %errorlevel%
