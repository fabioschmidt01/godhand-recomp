@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" call :setup || exit /b 1
".venv\Scripts\python.exe" -m recomp %*
exit /b %errorlevel%

:setup
echo Preparando o ambiente Python (so na primeira execucao)...
python -m venv .venv
if errorlevel 1 (
    echo Python 3.11 ou mais novo nao foi encontrado. Instale em https://www.python.org/downloads/ e rode de novo.
    exit /b 1
)
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
exit /b %errorlevel%
