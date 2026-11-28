@echo off
setlocal
set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"

echo Iniciando NBA Stats (Backend + Frontend)...

start "NBA Stats - Backend" /D "%ROOT%" cmd /k .venv\Scripts\python.exe -m uvicorn nba_stats.api.app:app --host 127.0.0.1 --port 8000
start "NBA Stats - Frontend" /D "%ROOT%\frontend" cmd /k npm run dev

echo Esperando a que el backend termine de iniciar en http://127.0.0.1:8000 ...
:wait_loop
curl -s http://127.0.0.1:8000/api/v1/health >nul 2>&1
if errorlevel 1 (
    timeout /t 1 /nobreak >nul
    goto wait_loop
)

echo Backend listo! Abriendo http://localhost:5173 ...
start "" http://localhost:5173
