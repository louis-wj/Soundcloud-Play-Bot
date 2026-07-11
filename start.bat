@echo off
title PlayBot + Mixcloud Launcher
color 0A

echo.
echo   PlayBot  ^|  SoundCloud + Mixcloud Automation
echo   ───────────────────────────────────────────────
echo.

:: Check Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo   [!] Python not found. Install Python 3.10+ from python.org
    pause & exit /b 1
)

:: Check Node
node --version >nul 2>&1
if %errorlevel% neq 0 (
    echo   [!] Node.js not found. Install from nodejs.org
    pause & exit /b 1
)

:: Check Chrome
reg query "HKCU\Software\Google\Chrome\BLBeacon" /v version >nul 2>&1
if %errorlevel% neq 0 (
    echo   [!] Chrome not found. Install from google.com/chrome
    pause & exit /b 1
)
echo   [OK] Python, Node and Chrome detected

:: Install backend deps (SoundCloud + Mixcloud)
echo   [1/3] Installing Python dependencies...
pip install -q fastapi uvicorn websockets undetected-chromedriver curl-cffi psutil requests selenium faker twocaptcha 2>nul

:: Install frontend deps if needed
echo   [2/3] Checking frontend dependencies...
if not exist "%~dp0frontend\node_modules" (
    echo   Installing npm packages...
    cd /d "%~dp0frontend"
    call npm install
    cd /d "%~dp0"
)

:: Kill anything already on port 8899
echo   Clearing port 8899...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8899 ^| findstr LISTENING') do (
    taskkill /F /PID %%a >nul 2>&1
)

:: Start backend in its own window
echo   [3/3] Starting backend server...
start "PlayBot Backend" cmd /k "cd /d "%~dp0backend" && python server.py"
timeout /t 3 /nobreak >nul

:: Start frontend dev server in its own window
echo   Starting frontend dev server...
start "PlayBot Frontend" cmd /k "cd /d "%~dp0frontend" && npm run dev"
timeout /t 2 /nobreak >nul

:: Open browser
start http://localhost:5173

echo.
echo   ✓ Backend  ^>  http://127.0.0.1:8899
echo   ✓ Frontend ^>  http://localhost:5173
echo.
echo   Close this window or press any key to stop both servers.
pause >nul
taskkill /f /fi "WINDOWTITLE eq PlayBot Backend" >nul 2>&1
taskkill /f /fi "WINDOWTITLE eq PlayBot Frontend" >nul 2>&1
