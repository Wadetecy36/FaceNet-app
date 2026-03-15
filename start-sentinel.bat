@echo off
:: ╔══════════════════════════════════════════════════════════════════╗
:: ║  SENTINEL // GREENWATCH  —  Full Stack Launcher (CMD)           ║
:: ║  start-sentinel.bat                                             ║
:: ╚══════════════════════════════════════════════════════════════════╝

setlocal EnableDelayedExpansion

:: ── User config ─────────────────────────────────────────────────────
set FACENET_DIR=F:\FaceNet
set FACENET_NODE=F:\FaceNet-Node
set PYTHON=python
set YOLO_HEALTH=http://localhost:8000/health
set YOLO_TIMEOUT=120
:: ────────────────────────────────────────────────────────────────────

title SENTINEL // GREENWATCH Launcher

color 0A
cls

echo.
echo  ============================================================
echo   SENTINEL ^|^| GREENWATCH  --  FaceNet-Node Stack Launcher
echo   Galamsey Detection System  ^|^|  ACity Tech Expo 2026
echo  ============================================================
echo.

:: ── Pre-flight checks ───────────────────────────────────────────────
echo  [*] PRE-FLIGHT CHECKS
echo  ------------------------------------------------------------

if not exist "%FACENET_DIR%" (
    echo  [X] ERROR: Directory not found: %FACENET_DIR%
    pause & exit /b 1
)
echo  [OK] FaceNet dir:  %FACENET_DIR%

if not exist "%FACENET_NODE%" (
    echo  [X] ERROR: Directory not found: %FACENET_NODE%
    pause & exit /b 1
)
echo  [OK] FaceNet-Node: %FACENET_NODE%

%PYTHON% --version >nul 2>&1
if errorlevel 1 (
    echo  [X] ERROR: Python not found. Check PYTHON variable.
    pause & exit /b 1
)
for /f "tokens=*" %%v in ('%PYTHON% --version 2^>^&1') do echo  [OK] %%v

echo.
echo  [*] LAUNCHING SERVICES
echo  ------------------------------------------------------------
echo.

:: ── 1. Flask DB logger ──────────────────────────────────────────────
echo  [1] Starting Flask DB Logger (port 5000)...
start "SENTINEL :: Flask Logger :5000" /D "%FACENET_DIR%" cmd /k "%PYTHON% flask_logger.py"
echo  [OK] Flask logger launched
timeout /t 2 /nobreak >nul

:: ── 2. YOLO inference server ─────────────────────────────────────────
echo  [2] Starting YOLO Inference Server (port 8000)...
start "SENTINEL :: YOLO Server :8000" /D "%FACENET_DIR%" cmd /k "uvicorn yolo_server:app --host 0.0.0.0 --port 8000"
echo  [OK] YOLO server launched
echo.

:: ── Wait for YOLO health ─────────────────────────────────────────────
echo  [~] Waiting for YOLO to load model...
echo      Polling: %YOLO_HEALTH%
echo.

set /a elapsed=0
set ready=0

:poll_loop
    :: Use PowerShell to do the HTTP check (more reliable than curl in stock Windows)
    for /f "tokens=*" %%s in ('powershell -NonInteractive -Command "try { $r = Invoke-WebRequest -Uri '%YOLO_HEALTH%' -UseBasicParsing -TimeoutSec 2; $r.StatusCode } catch { 0 }" 2^>nul') do (
        set status=%%s
    )

    if "!status!"=="200" (
        set ready=1
        goto yolo_ready
    )

    set /a elapsed+=2
    if !elapsed! GEQ %YOLO_TIMEOUT% goto yolo_timeout

    <nul set /p _="  [~] Still loading... !elapsed!s / %YOLO_TIMEOUT%s"
    echo.
    timeout /t 2 /nobreak >nul
    goto poll_loop

:yolo_timeout
    echo.
    echo  [X] YOLO server did not respond after %YOLO_TIMEOUT%s
    echo  [!] Live capture will NOT be started.
    echo  [!] Check the YOLO server terminal window for errors.
    echo.
    pause
    exit /b 1

:yolo_ready
    echo.
    echo  [OK] YOLO server READY after %elapsed%s
    echo.

:: ── 3. FaceNet Node API ──────────────────────────────────────────────
echo  [3] Starting FaceNet Node API (port 3001)...
start "SENTINEL :: Node API :3001" /D "%FACENET_NODE%" cmd /k "npm run server"
echo  [OK] Node API launched
timeout /t 2 /nobreak >nul

:: ── 4. FaceNet React UI ──────────────────────────────────────────────
echo  [4] Starting FaceNet React UI (port 5173)...
start "SENTINEL :: React UI :5173" /D "%FACENET_NODE%" cmd /k "npm run dev"
echo  [OK] React UI launched
timeout /t 1 /nobreak >nul

:: ── 5. Live capture ──────────────────────────────────────────────────
echo  [5] Starting Live Capture pipeline...
start "SENTINEL :: Live Capture" /D "%FACENET_DIR%" cmd /k "%PYTHON% live_capture.py"
echo  [OK] Live capture launched

:: ── Done ─────────────────────────────────────────────────────────────
echo.
echo  ============================================================
echo  [OK] ALL SERVICES RUNNING
echo.
echo      React UI   ^>  http://localhost:5173
echo      YOLO/WS    ^>  http://localhost:8000  ^|  ws://localhost:8000/ws
echo      Flask DB   ^>  http://localhost:5000
echo      Node API   ^>  http://localhost:3001
echo      Dashboard  ^>  file:///F:/FaceNet/dashboard.html
echo.
echo  Close individual terminal windows to stop services.
echo  ============================================================
echo.
pause