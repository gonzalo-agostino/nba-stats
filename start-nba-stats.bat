@echo off
setlocal
set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"

echo Iniciando NBA Stats (Backend + Frontend)...

start "NBA Stats - Backend" /D "%ROOT%" cmd /k .venv\Scripts\python.exe -m uvicorn nba_stats.api.app:app --host 127.0.0.1 --port 8000
start "NBA Stats - Frontend" /D "%ROOT%\frontend" cmd /k npm run dev

timeout /t 5 /nobreak >nul
start "" http://localhost:5173
