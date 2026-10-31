@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if errorlevel 1 (
    echo Python launcher was not found. Install Python 3.14 and enable the py launcher.
    pause
    exit /b 1
)
py -3.14 --version >nul 2>nul
if errorlevel 1 (
    echo Python 3.14 was not found. Install Python 3.14; this script will not select an older version.
    pause
    exit /b 1
)

if not exist "backend\.venv\Scripts\python.exe" (
    echo Creating Python 3.14 environment...
    py -3.14 -m venv backend\.venv
    if errorlevel 1 goto :failure
)
echo Installing backend requirements if needed...
backend\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
if errorlevel 1 goto :failure

if not exist "backend\.env" (
    copy "backend\.env.example" "backend\.env" >nul
    echo Created backend\.env. Configure a Gemini or Groq key; create the first admin before sign-in.
)

where node >nul 2>nul
if errorlevel 1 (
    echo Node.js was not found. Install Node.js 20 or newer.
    pause
    exit /b 1
)
if not exist "frontend\node_modules" (
    echo Installing frontend packages...
    pushd frontend
    call npm install
    if errorlevel 1 (
        popd
        goto :failure
    )
    popd
)

echo.
echo Backend: http://localhost:8001 (local only)
echo Frontend: http://localhost:5173
echo API docs: http://localhost:8001/docs
powershell -NoProfile -Command "Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254*' -and $_.InterfaceAlias -match 'Wi-Fi|Ethernet' } | ForEach-Object { Write-Output ('For your phone on the same Wi-Fi: http://' + $_.IPAddress + ':5173') }"
start "Inkwell API" cmd /k "cd /d ""%~dp0backend"" && ""%~dp0backend\.venv\Scripts\python.exe"" -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8001"
start "Inkwell frontend" cmd /k "cd /d ""%~dp0frontend"" && npm run dev"
timeout /t 3 /nobreak >nul
start "" "http://localhost:5173"
exit /b 0

:failure
echo.
echo Setup failed. Review the error above; no Python version was changed.
pause
exit /b 1
