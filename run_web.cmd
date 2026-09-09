@echo off
setlocal

cd /d "%~dp0"
set "PROJECT_PYTHON=%~dp0.venv\Scripts\python.exe"

if not exist "%PROJECT_PYTHON%" (
    echo [ERROR] Project virtual environment not found: .venv\Scripts\python.exe
    echo Create the virtual environment and install project dependencies first.
    pause
    exit /b 1
)

set "PYTHONUTF8=1"
set "PYTHONUNBUFFERED=1"

echo Starting WeChat Article Reader...
echo Web URL: http://127.0.0.1:8000
echo Press Ctrl+C to stop the service.
echo.

"%PROJECT_PYTHON%" -m wechat_article_reader
set "WEB_EXIT_CODE=%ERRORLEVEL%"

if not "%WEB_EXIT_CODE%"=="0" (
    echo.
    echo [ERROR] Web service exited with code %WEB_EXIT_CODE%.
    pause
)

exit /b %WEB_EXIT_CODE%
