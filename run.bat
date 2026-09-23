@echo off
REM Windows: creates a virtualenv, installs dependencies and starts the gateway.
cd /d "%~dp0"
if not exist .venv ( python -m venv .venv )
call .venv\Scripts\activate.bat
pip install -q -r requirements.txt
if exist .env ( for /f "usebackq eol=# tokens=1,* delims==" %%a in (".env") do if not "%%b"=="" set "%%a=%%b" )
echo.
echo   Dashboard:  http://localhost:8000/dashboard
echo   API docs:   http://localhost:8000/docs
echo.
uvicorn app.main:app --host 127.0.0.1 --port 8000
