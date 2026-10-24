@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" goto run
echo Creating a local Python environment...
where py >nul 2>nul
if errorlevel 1 (
    python -m venv .venv
) else (
    py -3 -m venv .venv
)
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed
:run
echo.
echo LAITYS WB Study: http://127.0.0.1:8000
echo API documentation: http://127.0.0.1:8000/docs
echo Stop the server with Ctrl+C.
".venv\Scripts\python.exe" -m uvicorn app_final:app --host 127.0.0.1 --port 8000
if errorlevel 1 goto failed
exit /b 0
:failed
echo.
echo Could not start. See the error above and README.md.
echo Python 3.11 or newer and internet for the first install are required.
pause
exit /b 1
