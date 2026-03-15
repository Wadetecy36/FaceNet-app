# =============================================================================
#  SENTINEL // GREENWATCH  -  Full Stack Launcher
#  start_ps.ps1
#
#  Run from F:\FaceNet :
#    powershell -ExecutionPolicy Bypass -File .\start_ps.ps1
#
#  Launch order:
#    1. Flask DB logger      (port 5000)
#    2. YOLO inference server (port 8000)  <- waits for /health OK
#    3. FaceNet Node API     (port 3001)
#    4. FaceNet React UI     (port 5173)
#    5. Live capture         (camera pipeline)
# =============================================================================

# ---- USER CONFIG -------------------------------------------------------------
$FACENET_DIR  = "F:\FaceNet"
$FACENET_NODE = "F:\FaceNet-Node"
$PYTHON       = "python"
$YOLO_HEALTH  = "http://localhost:8000/health"
$YOLO_TIMEOUT = 120      # max seconds to wait for YOLO
$POLL_SECS    = 2        # seconds between health polls
# ------------------------------------------------------------------------------

$Host.UI.RawUI.WindowTitle = "SENTINEL // GREENWATCH"

# ---- Helpers -----------------------------------------------------------------

function Print-OK($msg) {
    Write-Host "  [OK]  " -NoNewline -ForegroundColor Green
    Write-Host $msg
}

function Print-INFO($msg) {
    Write-Host "  [..]  " -NoNewline -ForegroundColor Cyan
    Write-Host $msg -ForegroundColor DarkGray
}

function Print-WARN($msg) {
    Write-Host "  [!!]  " -NoNewline -ForegroundColor Yellow
    Write-Host $msg -ForegroundColor Yellow
}

function Print-ERR($msg) {
    Write-Host "  [XX]  " -NoNewline -ForegroundColor Red
    Write-Host $msg -ForegroundColor Red
}

function Print-STEP($n, $msg) {
    Write-Host "  [$n]  " -NoNewline -ForegroundColor Cyan
    Write-Host $msg -ForegroundColor White
}

function Print-HR {
    Write-Host "  " -NoNewline
    Write-Host ("-" * 62) -ForegroundColor DarkGray
}

function Print-Banner {
    Clear-Host
    Write-Host ""
    Write-Host "  ============================================================" -ForegroundColor DarkGreen
    Write-Host "   SENTINEL  //  GREENWATCH  //  FaceNet-Node Stack" -ForegroundColor Green
    Write-Host "   Galamsey Detection System  //  ACity Tech Expo 2026" -ForegroundColor DarkYellow
    Write-Host "  ============================================================" -ForegroundColor DarkGreen
    Write-Host ""
}

# ---- Wait for YOLO health ----------------------------------------------------

function Wait-YOLO {
    Write-Host ""
    Print-STEP "~" "Waiting for YOLO server to finish loading..."
    Print-INFO "Polling $YOLO_HEALTH every ${POLL_SECS}s  (timeout: ${YOLO_TIMEOUT}s)"
    Write-Host ""

    $elapsed = 0
    $spin    = @("|", "/", "-", "\")
    $si      = 0

    while ($elapsed -lt $YOLO_TIMEOUT) {

        # HTTP check via .NET (no curl dependency)
        $ready = $false
        $yoloName = "?"
        $llmName  = "?"
        try {
            $req  = [System.Net.WebRequest]::Create($YOLO_HEALTH)
            $req.Timeout = 2000
            $resp = $req.GetResponse()
            $code = [int]$resp.StatusCode
            if ($code -eq 200) {
                $reader  = New-Object System.IO.StreamReader($resp.GetResponseStream())
                $body    = $reader.ReadToEnd() | ConvertFrom-Json
                $yoloName = $body.yolo
                $llmName  = $body.llm
                $ready = $true
            }
            $resp.Close()
        } catch {
            # not up yet - continue polling
        }

        if ($ready) {
            Write-Host ""
            Print-OK "YOLO server READY  |  model=$yoloName  llm=$llmName  (${elapsed}s)"
            Write-Host ""
            return $true
        }

        $dot = $spin[$si % 4]
        $bar_filled = [int]($elapsed / $YOLO_TIMEOUT * 30)
        $bar_empty  = 30 - $bar_filled
        $bar = ("#" * $bar_filled) + ("." * $bar_empty)
        $pct = [int]($elapsed / $YOLO_TIMEOUT * 100)

        Write-Host "`r  [$dot]  [$bar] $pct%  ${elapsed}s / ${YOLO_TIMEOUT}s   " `
            -NoNewline -ForegroundColor DarkYellow

        Start-Sleep -Seconds $POLL_SECS
        $elapsed += $POLL_SECS
        $si++
    }

    Write-Host ""
    Print-ERR "YOLO server did not respond after ${YOLO_TIMEOUT}s"
    return $false
}

# ---- Launch a service in a new window ----------------------------------------

function Start-Svc($title, $dir, $cmd) {
    $proc = Start-Process cmd.exe `
        -ArgumentList "/k title $title && $cmd" `
        -WorkingDirectory $dir `
        -PassThru
    return $proc
}

# ==============================================================================
#  MAIN
# ==============================================================================

Print-Banner

# -- Pre-flight checks ---------------------------------------------------------
Write-Host "  PRE-FLIGHT CHECKS" -ForegroundColor DarkYellow
Print-HR

if (-not (Test-Path $FACENET_DIR)) {
    Print-ERR "Directory not found: $FACENET_DIR"
    pause; exit 1
}
Print-OK "FaceNet dir:    $FACENET_DIR"

if (-not (Test-Path $FACENET_NODE)) {
    Print-ERR "Directory not found: $FACENET_NODE"
    pause; exit 1
}
Print-OK "FaceNet-Node:   $FACENET_NODE"

$pyCheck = & $PYTHON --version 2>&1
if ($LASTEXITCODE -ne 0) {
    Print-ERR "Python not found. Update PYTHON variable at top of script."
    pause; exit 1
}
Print-OK "Python:         $pyCheck"

Write-Host ""
Write-Host "  CAMERA SETUP" -ForegroundColor DarkYellow
Print-HR
Write-Host ""
Write-Host "  [1]  Phone camera  (IP Webcam app on Android)" -ForegroundColor White
Write-Host "  [2]  Local webcam  (built-in or USB)" -ForegroundColor White
Write-Host ""

$camChoice = ""
while ($camChoice -ne "1" -and $camChoice -ne "2") {
    $camChoice = Read-Host "  Select [1/2]"
    if ($camChoice -eq "") { $camChoice = "1" }
    if ($camChoice -ne "1" -and $camChoice -ne "2") {
        Print-WARN "Please enter 1 or 2"
    }
}

$CAPTURE_ARGS = ""

if ($camChoice -eq "1") {
    Write-Host ""
    Write-Host "  Steps if you haven't already:" -ForegroundColor DarkGray
    Write-Host "    1. Install 'IP Webcam' from Google Play" -ForegroundColor DarkGray
    Write-Host "    2. Open app -> scroll down -> 'Start server'" -ForegroundColor DarkGray
    Write-Host "    3. Note the IP shown at the bottom of the app screen" -ForegroundColor DarkGray
    Write-Host "    4. Phone and this PC must be on the same WiFi network" -ForegroundColor DarkGray
    Write-Host ""

    $phoneIP = ""
    while ($true) {
        $phoneIP = Read-Host "  Phone IP address (e.g. 192.168.1.45)"
        if ($phoneIP -eq "") {
            Print-WARN "IP address cannot be empty"
            continue
        }
        # Split off port if user typed IP:port
        $ipPart   = $phoneIP.Split(":")[0]
        $portPart = if ($phoneIP.Contains(":")) { $phoneIP.Split(":")[1] } else { "8080" }

        # Basic validation: 4 octets
        $octets = $ipPart.Split(".")
        $valid  = ($octets.Count -eq 4)
        if ($valid) {
            foreach ($o in $octets) {
                $n = 0
                if (-not ([int]::TryParse($o, [ref]$n)) -or $n -lt 0 -or $n -gt 255) {
                    $valid = $false
                    break
                }
            }
        }

        if ($valid) {
            Print-OK "Phone IP: $ipPart  Port: $portPart"
            $CAPTURE_ARGS = "--ip $ipPart --port $portPart"
            break
        } else {
            Print-WARN "'$phoneIP' is not a valid IP address (expected format: 192.168.1.45)"
        }
    }
} else {
    Write-Host ""
    $camIdx = Read-Host "  Webcam index (default: 0)"
    if ($camIdx -eq "") { $camIdx = "0" }
    Print-OK "Local webcam index: $camIdx"
    $CAPTURE_ARGS = "--local --index $camIdx"
}

Write-Host ""
Write-Host "  LAUNCHING SERVICES" -ForegroundColor DarkYellow
Print-HR
Write-Host ""

$procs = @()

# -- 1. Flask DB logger --------------------------------------------------------
Print-STEP "1" "Starting Flask DB Logger  (port 5000)..."
$p1 = Start-Svc `
    "SENTINEL :: Flask :5000" `
    $FACENET_DIR `
    "$PYTHON flask_logger.py"
$procs += $p1.Id
Print-OK "Flask logger started  [PID $($p1.Id)]"
Start-Sleep -Seconds 2

# -- 2. YOLO inference server --------------------------------------------------
Print-STEP "2" "Starting YOLO Inference Server  (port 8000)..."
$p2 = Start-Svc `
    "SENTINEL :: YOLO :8000" `
    $FACENET_DIR `
    "uvicorn yolo_server:app --host 0.0.0.0 --port 8000"
$procs += $p2.Id
Print-OK "YOLO server started  [PID $($p2.Id)]"

# -- Wait for YOLO /health -----------------------------------------------------
$ready = Wait-YOLO
if (-not $ready) {
    Print-ERR "Aborting. Check the YOLO server window for errors."
    Print-WARN "Live capture was NOT started."
    Write-Host ""
    pause
    exit 1
}

# -- 3. FaceNet Node API -------------------------------------------------------
Print-STEP "3" "Starting FaceNet Node API  (port 3001)..."
$p3 = Start-Svc `
    "SENTINEL :: Node API :3001" `
    $FACENET_NODE `
    "npm run server"
$procs += $p3.Id
Print-OK "Node API started  [PID $($p3.Id)]"
Start-Sleep -Seconds 2

# -- 4. FaceNet React UI -------------------------------------------------------
Print-STEP "4" "Starting FaceNet React UI  (port 5173)..."
$p4 = Start-Svc `
    "SENTINEL :: React UI :5173" `
    $FACENET_NODE `
    "npm run dev"
$procs += $p4.Id
Print-OK "React UI started  [PID $($p4.Id)]"
Start-Sleep -Seconds 1

# -- 5. Live capture -----------------------------------------------------------
Print-STEP "5" "Starting Live Capture pipeline..."
$p5 = Start-Svc `
    "SENTINEL :: Live Capture" `
    $FACENET_DIR `
    "$PYTHON live_capture.py $CAPTURE_ARGS"
$procs += $p5.Id
Print-OK "Live capture started  [PID $($p5.Id)]"

# -- Summary -------------------------------------------------------------------
Write-Host ""
Print-HR
Write-Host ""
Print-OK "ALL SERVICES RUNNING"
Write-Host ""
Write-Host "  ENDPOINTS" -ForegroundColor DarkYellow
Write-Host ""
Print-INFO "React UI    ->  http://localhost:5173"
Print-INFO "YOLO / WS   ->  http://localhost:8000  |  ws://localhost:8000/ws"
Print-INFO "Flask DB    ->  http://localhost:5000"
Print-INFO "Node API    ->  http://localhost:3001"
Print-INFO "Dashboard   ->  file:///F:/FaceNet/dashboard.html"
Write-Host ""
Write-Host "  PROCESS IDs" -ForegroundColor DarkYellow
Write-Host ""
$labels = @("Flask  :5000", "YOLO   :8000", "Node   :3001", "React  :5173", "Capture     ")
for ($i = 0; $i -lt $procs.Count; $i++) {
    Print-INFO "$($labels[$i])   PID $($procs[$i])"
}
Write-Host ""
Print-HR
Write-Host ""
Write-Host "  Close the individual terminal windows to stop each service." -ForegroundColor DarkGray
Write-Host "  Press ENTER here to kill ALL processes and exit." -ForegroundColor DarkGray
Write-Host ""
Read-Host "  > Press ENTER to shut down"

# -- Cleanup -------------------------------------------------------------------
Write-Host ""
Print-WARN "Shutting down all services..."
foreach ($id in $procs) {
    try {
        Stop-Process -Id $id -Force -ErrorAction SilentlyContinue
        Print-INFO "Killed PID $id"
    } catch {
        # already gone
    }
}
# Also kill any orphan node/python processes by window title substring
Get-Process | Where-Object { $_.MainWindowTitle -like "SENTINEL*" } | ForEach-Object {
    $_.Kill()
    Print-INFO "Killed orphan: $($_.MainWindowTitle)"
}
Print-OK "All services terminated."
Start-Sleep -Seconds 1