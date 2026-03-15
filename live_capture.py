"""
live_capture.py  —  SENTINEL // GreenWatch Camera Pipeline
══════════════════════════════════════════════════════════════
Captures frames from a phone camera (IP Webcam) or local webcam,
sends them to the YOLO inference server, and logs results.

Startup behaviour:
  • Polls /health until YOLO server is ready (with spinner)
  • Only then opens camera and begins inference loop

Usage:
  python live_capture.py

Dependencies:
  pip install opencv-python requests pillow numpy
"""

import base64
import json
import os
import sys
import time
import uuid
import socket
import threading
import argparse
import requests
import cv2
import numpy as np
from datetime import datetime

# ╔══════════════════════════════════════════════════════════════════╗
# ║                   ★  CAMERA CONFIG  ★                           ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║                                                                  ║
# ║  OPTION A — Phone camera (recommended for demo):                 ║
# ║    1. Install "IP Webcam" on your Android phone                  ║
# ║    2. Open the app → tap "Start server"                          ║
# ║    3. Note the IP shown (e.g. 192.168.1.45:8080)               ║
# ║    4. Set PHONE_IP below and set CAMERA_SOURCE = "phone"         ║
# ║                                                                  ║
# ║  OPTION B — Local webcam:                                        ║
# ║    Set CAMERA_SOURCE = "local"                                   ║
# ║    Set LOCAL_CAM_INDEX = 0  (try 1, 2 if 0 fails)              ║
# ║                                                                  ║
# ╠══════════════════════════════════════════════════════════════════╣

CAMERA_SOURCE    = "phone"            # "phone" | "local"

# ┌─────────────────────────────────────────────────────────┐
# │  ← SET THIS to your phone's IP address (from IP Webcam) │
PHONE_IP         = "192.168.1.100"   #                     │
# └─────────────────────────────────────────────────────────┘

PHONE_PORT       = 8080               # IP Webcam default port (usually 8080)
LOCAL_CAM_INDEX  = 0                  # 0 = default webcam, 1 = second camera

# ── Server endpoints ──────────────────────────────────────────────
INFER_URL        = "http://localhost:8000/infer"
HEALTH_URL       = "http://localhost:8000/health"
FACENET_URL      = "http://localhost:3001/api/users"

# ── Session metadata ──────────────────────────────────────────────
DEVICE_ID        = socket.gethostname()
LOCATION         = "Surveillance Zone A"   # appears in dashboard
SESSION_ID       = str(uuid.uuid4())[:12]

# ── Capture settings ──────────────────────────────────────────────
# Camera capture runs in its own thread as fast as possible.
# Inference runs in a separate thread — the two never block each other.

CAPTURE_FPS      = 15     # max frames/sec the capture thread grabs
                          # (phone camera HTTP usually tops out at 10-15)
INFER_FPS        = 6      # max inference requests/sec sent to YOLO
                          # (keeps YOLO from getting flooded; raise if GPU)

INFER_W          = 640    # resize width before encoding — cuts payload ~75%
JPEG_QUALITY     = 60     # 0-100 lower = smaller payload = faster (60 is fine)

HEALTH_TIMEOUT   = 120    # max seconds to wait for YOLO server
HEALTH_POLL_S    = 1.5    # seconds between health polls
FACES_REFRESH_S  = 30     # re-fetch known faces every N seconds

# ╚══════════════════════════════════════════════════════════════════╝


# ─── Terminal colours ─────────────────────────────────────────────────────────

if sys.platform == "win32":
    # Enable ANSI escape codes in Windows cmd/PowerShell
    import ctypes
    try:
        ctypes.windll.kernel32.SetConsoleMode(
            ctypes.windll.kernel32.GetStdHandle(-11), 7)
    except Exception:
        pass

R = "\033[0m"          # reset
BOLD  = "\033[1m"
DIM   = "\033[2m"
GREEN = "\033[92m"
AMBER = "\033[93m"
CYAN  = "\033[96m"
RED   = "\033[91m"
BLUE  = "\033[94m"
GRAY  = "\033[90m"
WHITE = "\033[97m"
BRED  = "\033[41m"     # bg red

def C(color, text):   return f"{color}{text}{R}"
def ok(msg):          print(f"  {C(GREEN, '✔')}  {msg}")
def info(msg):        print(f"  {C(CYAN,  '·')}  {C(GRAY, msg)}")
def warn(msg):        print(f"  {C(AMBER, '!')}  {C(AMBER, msg)}")
def err(msg):         print(f"  {C(RED,   '✘')}  {C(RED, msg)}")
def hr(ch="─", n=62): print(f"  {C(GRAY, ch * n)}")


# ─── Banner ───────────────────────────────────────────────────────────────────

def print_banner():
    print()
    print(C(GREEN,  "  ╔════════════════════════════════════════════════════════╗"))
    print(C(GREEN,  "  ║") +
          C(AMBER,  "  ▶  SENTINEL // GREENWATCH  —  Live Capture Pipeline   ") +
          C(GREEN,  "║"))
    print(C(GREEN,  "  ╠════════════════════════════════════════════════════════╣"))

    cam_val = (f"{PHONE_IP}:{PHONE_PORT}/shot.jpg"
               if CAMERA_SOURCE == "phone" else f"local (index {LOCAL_CAM_INDEX})")

    rows = [
        ("SESSION",  SESSION_ID,  CYAN),
        ("DEVICE",   DEVICE_ID,   WHITE),
        ("LOCATION", LOCATION,    WHITE),
        ("CAMERA",   cam_val,     AMBER),
        ("FPS",      f"~{int(1/FRAME_INTERVAL)} (interval {FRAME_INTERVAL}s)", WHITE),
        ("INFER",    INFER_URL,   BLUE),
    ]
    for label, val, col in rows:
        pad = f"{label:<10}"
        print(C(GREEN, "  ║") + f"  {C(GRAY, pad)}  {C(col, val):<55}" + C(GREEN, "║"))

    print(C(GREEN, "  ╚════════════════════════════════════════════════════════╝"))
    print()


# ─── Wait for YOLO server ────────────────────────────────────────────────────

SPIN = ["⠋","⠙","⠹","⠸","⠼","⠴","⠦","⠧","⠇","⠏"]

def ask_camera_config():
    """
    Interactive prompt at startup.
    Asks whether to use phone camera or local webcam.
    If phone, prompts for IP address with validation.
    Updates the global CAMERA_SOURCE, PHONE_IP, LOCAL_CAM_INDEX.
    """
    global CAMERA_SOURCE, PHONE_IP, LOCAL_CAM_INDEX

    print(C(AMBER, "  CAMERA SETUP"))
    hr()
    print()
    print(f"  {C(CYAN,  '[1]')}  {C(WHITE, 'Phone camera')}  {C(GRAY, '(IP Webcam app on Android)')}")
    print(f"  {C(CYAN,  '[2]')}  {C(WHITE, 'Local webcam')}  {C(GRAY, '(built-in or USB camera)')}")
    print()

    while True:
        try:
            raw = input(f"  {C(AMBER, 'Select [1/2]')} > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            sys.exit(0)

        if raw in ("1", ""):
            CAMERA_SOURCE = "phone"
            break
        elif raw == "2":
            CAMERA_SOURCE = "local"
            break
        else:
            warn("Please enter 1 or 2")

    # ── Phone path: ask for IP ──────────────────────────────────────────────
    if CAMERA_SOURCE == "phone":
        print()
        print(C(GRAY,  "  Steps if you haven't already:"))
        print(C(GRAY,  "    1. Install 'IP Webcam' from Google Play"))
        print(C(GRAY,  "    2. Open app  ->  scroll down  ->  'Start server'"))
        print(C(GRAY,  "    3. The IP address is shown at the bottom of the app"))
        print(C(GRAY,  f"    4. Phone and this PC must be on the same WiFi"))
        print()

        while True:
            try:
                default_hint = f"  {C(GRAY, '(default: ' + PHONE_IP + ')')} " if PHONE_IP != "192.168.1.100" else ""
                prompt = f"  {C(AMBER, 'Phone IP address')} {default_hint}> "
                raw = input(prompt).strip()
            except (EOFError, KeyboardInterrupt):
                print()
                sys.exit(0)

            # Accept blank = keep existing default
            if raw == "":
                print()
                info(f"Using existing default: {C(AMBER, PHONE_IP)}")
                break

            # Basic IP validation: four octets of 0-255, optional :port
            import re as _re
            # strip port if provided
            ip_part   = raw.split(":")[0]
            port_part = raw.split(":")[1] if ":" in raw else None

            octets = ip_part.split(".")
            if (len(octets) == 4 and
                    all(o.isdigit() and 0 <= int(o) <= 255 for o in octets)):
                PHONE_IP = ip_part
                if port_part and port_part.isdigit():
                    global PHONE_PORT
                    PHONE_PORT = int(port_part)
                    info(f"Port set to {PHONE_PORT}")
                print()
                ok(f"Phone IP set to {C(AMBER, PHONE_IP)}:{PHONE_PORT}")
                break
            else:
                warn(f"'{raw}' doesn't look like a valid IP address")
                warn("Expected format: 192.168.1.45   or   192.168.1.45:8080")

    # ── Local webcam path: ask for index ───────────────────────────────────
    else:
        print()
        try:
            raw = input(f"  {C(AMBER, 'Webcam index')} {C(GRAY, '(default: 0)')} > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            sys.exit(0)
        if raw.isdigit():
            LOCAL_CAM_INDEX = int(raw)
        ok(f"Local webcam index: {C(AMBER, str(LOCAL_CAM_INDEX))}")

    print()
    hr()
    print()


def wait_for_yolo() -> bool:
    """Poll /health until 200 OK or timeout. Returns True if ready."""
    print(C(AMBER, f"  ⏳  Waiting for YOLO server to finish loading..."))
    info(f"Polling {HEALTH_URL}  (timeout: {HEALTH_TIMEOUT}s)")
    print()

    elapsed = 0.0
    si      = 0

    while elapsed < HEALTH_TIMEOUT:
        try:
            r = requests.get(HEALTH_URL, timeout=2)
            if r.status_code == 200:
                body = r.json()
                print(f"\r  {C(GREEN,'✔')}  YOLO server {C(GREEN,'READY')} "
                      f"{C(GRAY,'│')} "
                      f"model={C(AMBER, body.get('yolo','?'))}  "
                      f"llm={C(CYAN, body.get('llm','?'))}  "
                      f"{C(GRAY, f'({elapsed:.0f}s)')}          ")
                print()
                return True
        except Exception:
            pass

        dot = SPIN[si % len(SPIN)]
        bar_filled = int((elapsed / HEALTH_TIMEOUT) * 20)
        bar = C(GREEN, "█" * bar_filled) + C(GRAY, "░" * (20 - bar_filled))
        pct = f"{int(elapsed/HEALTH_TIMEOUT*100):3d}%"
        print(f"\r  {C(AMBER,dot)}  [{bar}] {C(AMBER, pct)}  {C(GRAY, f'{elapsed:.0f}s')}", end="", flush=True)

        time.sleep(HEALTH_POLL_S)
        elapsed += HEALTH_POLL_S
        si += 1

    print()
    err(f"YOLO server did not respond after {HEALTH_TIMEOUT}s")
    warn("Check the YOLO server terminal for errors.")
    return False


# ─── Known face cache ─────────────────────────────────────────────────────────

_known_faces     = []
_faces_lock      = threading.Lock()
_faces_last_upd  = 0.0


def refresh_known_faces() -> int:
    global _known_faces, _faces_last_upd
    try:
        r = requests.get(FACENET_URL, timeout=5)
        if r.status_code == 200:
            users = r.json()
            faces = [
                {"id": u["name"], "name": u["name"], "confidence": 0.95}
                for u in users if u.get("encoding_json")
            ]
            with _faces_lock:
                _known_faces    = faces
                _faces_last_upd = time.time()
            return len(faces)
    except Exception as e:
        warn(f"Could not fetch known faces from {FACENET_URL}: {e}")
    return 0


def get_known_faces() -> list:
    if time.time() - _faces_last_upd > FACES_REFRESH_S:
        refresh_known_faces()
    with _faces_lock:
        return list(_known_faces)


# ─── Frame helpers ────────────────────────────────────────────────────────────

def grab_phone_frame() -> np.ndarray:
    url = f"http://{PHONE_IP}:{PHONE_PORT}/shot.jpg"
    r   = requests.get(url, timeout=5)
    r.raise_for_status()
    buf = np.frombuffer(r.content, dtype=np.uint8)
    img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("Could not decode frame from phone camera")
    return img


def encode_frame(frame: np.ndarray) -> str:
    """Resize to INFER_W and encode as base64 JPEG. Smaller = faster upload."""
    h, w = frame.shape[:2]
    if w > INFER_W:
        frame = cv2.resize(frame, (INFER_W, int(h * INFER_W / w)),
                           interpolation=cv2.INTER_AREA)
    _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
    return base64.b64encode(buf).decode("utf-8")


# ─── Severity helpers ─────────────────────────────────────────────────────────

_SEV_COL = {"high": RED, "medium": AMBER, "low": DIM + AMBER, "none": GRAY}

def sev_badge(s: str) -> str:
    col   = _SEV_COL.get(s.lower(), GRAY)
    label = f"{s.upper():<6}"
    return C(col, f"[{label}]")


# ─── Shared state between capture and inference threads ──────────────────────

_frame_slot      : np.ndarray | None = None   # latest captured frame
_frame_slot_lock = threading.Lock()
_frame_ready     = threading.Event()           # set when a new frame is available
_stop_event      = threading.Event()           # set to request clean shutdown

# Stats (written by infer thread, read by print loop)
_stats_lock      = threading.Lock()
_stats           = {
    "frame_cap":  0,   # total frames captured
    "frame_infer":0,   # total frames sent for inference
    "frame_skip": 0,   # frames dropped (infer was busy)
    "cap_fps":    0.0,
    "infer_fps":  0.0,
    "last_ms":    0.0,
    "last_sev":   "none",
    "last_persons":0,
    "last_unknown":0,
    "last_dets":  [],
    "last_anoms": [],
}


# ─── Capture thread ───────────────────────────────────────────────────────────

def _capture_loop(cap):
    """
    Runs in its own thread.
    Grabs frames as fast as CAPTURE_FPS allows and stores the latest
    in _frame_slot. The inference thread reads from there independently.
    """
    global _frame_slot

    interval   = 1.0 / CAPTURE_FPS
    err_streak = 0
    fps_t0     = time.time()
    fps_count  = 0

    while not _stop_event.is_set():
        t0 = time.time()

        try:
            if CAMERA_SOURCE == "phone":
                frame = grab_phone_frame()
            else:
                ok_flag, frame = cap.read()
                if not ok_flag:
                    raise RuntimeError("Webcam read() returned False")
            err_streak = 0

        except Exception as e:
            err_streak += 1
            if err_streak == 1 or err_streak % 20 == 0:
                warn(f"Camera error #{err_streak}: {e}")
            time.sleep(min(err_streak * 0.5, 5.0))
            continue

        # Store latest frame (always overwrite — inference picks up newest)
        with _frame_slot_lock:
            _frame_slot = frame

        _frame_ready.set()

        # FPS accounting
        fps_count += 1
        with _stats_lock:
            _stats["frame_cap"] += 1
        elapsed = time.time() - fps_t0
        if elapsed >= 2.0:
            with _stats_lock:
                _stats["cap_fps"] = round(fps_count / elapsed, 1)
            fps_t0    = time.time()
            fps_count = 0

        # Pace to CAPTURE_FPS
        sleep = max(0.0, interval - (time.time() - t0))
        if sleep > 0:
            time.sleep(sleep)

    if cap and CAMERA_SOURCE == "local":
        cap.release()


# ─── Inference thread ─────────────────────────────────────────────────────────

def _infer_loop():
    """
    Runs in its own thread.
    Picks up the latest frame from _frame_slot and POSTs to YOLO.
    Never waits for capture — always uses the freshest available frame.
    """
    session    = requests.Session()   # reuse TCP connection
    interval   = 1.0 / INFER_FPS
    last_frame = None                 # track if frame actually changed
    fps_t0     = time.time()
    fps_count  = 0

    while not _stop_event.is_set():
        t0 = time.time()

        # Wait for a new frame (with short timeout so we can check stop_event)
        _frame_ready.wait(timeout=1.0)
        _frame_ready.clear()

        with _frame_slot_lock:
            frame = _frame_slot

        if frame is None:
            continue

        # Skip if frame identical to last sent (avoids flooding on slow cameras)
        frame_id = id(frame)
        if frame_id == id(last_frame):
            with _stats_lock:
                _stats["frame_skip"] += 1
            # pace anyway
            time.sleep(max(0.0, interval - (time.time() - t0)))
            continue
        last_frame = frame

        # Encode and send
        known   = get_known_faces()
        payload = {
            "frame_b64":   encode_frame(frame),
            "device_id":   DEVICE_ID,
            "session_id":  SESSION_ID,
            "timestamp":   time.time(),
            "known_faces": known,
            "location":    LOCATION,
        }

        try:
            resp = session.post(INFER_URL, json=payload, timeout=20)
            data = resp.json()
        except Exception as e:
            warn(f"Inference request failed: {e}")
            time.sleep(1.0)
            continue

        # Update shared stats
        fps_count += 1
        elapsed = time.time() - fps_t0
        if elapsed >= 2.0:
            with _stats_lock:
                _stats["infer_fps"] = round(fps_count / elapsed, 1)
            fps_t0    = time.time()
            fps_count = 0

        with _stats_lock:
            _stats["frame_infer"]  += 1
            _stats["last_ms"]       = float(data.get("processing_ms", 0))
            _stats["last_sev"]      = data.get("max_severity", "none")
            _stats["last_persons"]  = data.get("person_count",  0)
            _stats["last_unknown"]  = data.get("unknown_count", 0)
            _stats["last_dets"]     = data.get("detections",    [])
            _stats["last_anoms"]    = data.get("anomalies",     [])

        # Pace to INFER_FPS (don't flood YOLO faster than it can respond)
        sleep = max(0.0, interval - (time.time() - t0))
        if sleep > 0:
            time.sleep(sleep)


# ─── Print loop ───────────────────────────────────────────────────────────────

def _print_loop():
    """Prints a status line every second — decoupled from capture and infer."""
    line_n = 0
    while not _stop_event.is_set():
        time.sleep(1.0)
        line_n += 1

        with _stats_lock:
            s = dict(_stats)

        ts      = datetime.now().strftime("%H:%M:%S")
        sev_s   = sev_badge(s["last_sev"])
        ms      = s["last_ms"]
        ms_col  = GREEN if ms < 500 else (AMBER if ms < 1200 else RED)
        p_col   = RED   if s["last_persons"] > 5 else (AMBER if s["last_persons"] > 0 else GRAY)
        uk_col  = RED   if s["last_unknown"] > 0 else GRAY

        det_parts = [f"{d['label']}({int(d['confidence']*100)}%)" for d in s["last_dets"][:4]]
        if len(s["last_dets"]) > 4:
            det_parts.append(f"+{len(s['last_dets'])-4}")
        det_str = "  ".join(det_parts) or C(GRAY, "—")

        cap_fps_s   = C(CYAN,  f"{s['cap_fps']:>4.1f}fps")
        infer_fps_s = C(AMBER, f"{s['infer_fps']:>4.1f}fps")
        skip_s      = C(GRAY,  f"skip={s['frame_skip']}")

        print(
            f"  {C(GRAY,ts)}  "
            f"cap={cap_fps_s} inf={infer_fps_s} {skip_s}  "
            f"{sev_s}  "
            f"{C(p_col,  str(s['last_persons'])+'p')}"
            f" {C(uk_col, str(s['last_unknown'])+'unk')}  "
            f"{C(ms_col, str(int(ms))+'ms')}  "
            f"{C(GRAY, det_str)}"
        )

        # Print column headers every 20 lines so they stay visible
        if line_n % 20 == 0:
            print()
            print(
                f"  {C(GRAY,'TIME     ')}  "
                f"{C(GRAY,'cap=capFPS inf=inferFPS skip=dropped')}  "
                f"{C(GRAY,'SEVERITY')}  "
                f"{C(GRAY,'PERSONS UNK')}  "
                f"{C(GRAY,'MS')}  "
                f"{C(GRAY,'DETECTIONS')}"
            )
            hr("·")

        # High alert banner
        anoms = s["last_anoms"]
        if s["last_sev"] == "high" and anoms:
            print()
            print(f"  {C(BRED, ' ██ HIGH ALERT ██ ')} {C(RED, anoms[0].get('description','')[:80])}")
            print()


# ─── Main: run_capture ────────────────────────────────────────────────────────

def run_capture():
    """
    Starts three threads:
      1. Capture thread  — grabs frames at CAPTURE_FPS
      2. Inference thread — sends frames to YOLO at INFER_FPS
      3. Print thread    — shows a status line every second

    Threads run independently so a slow Gemma response never stalls
    the camera feed or dashboard updates.
    """
    cap = None

    # Open camera
    if CAMERA_SOURCE == "local":
        info(f"Opening local camera index {LOCAL_CAM_INDEX}...")
        cap = cv2.VideoCapture(LOCAL_CAM_INDEX)
        if not cap.isOpened():
            err(f"Cannot open webcam index {LOCAL_CAM_INDEX}")
            return
        # Request higher resolution & frame rate from the driver
        cap.set(cv2.CAP_PROP_FRAME_WIDTH,  1280)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        cap.set(cv2.CAP_PROP_FPS, CAPTURE_FPS)
        ok("Local camera opened")
    else:
        phone_url = f"http://{PHONE_IP}:{PHONE_PORT}/shot.jpg"
        info(f"Testing phone camera: {phone_url}")
        try:
            requests.get(phone_url, timeout=4)
            ok(f"Phone camera reachable at {PHONE_IP}:{PHONE_PORT}")
        except Exception:
            warn(f"Phone at {PHONE_IP}:{PHONE_PORT} not responding — will retry each frame")
            warn("Make sure IP Webcam app is running and phone is on the same WiFi")

    print()
    print(
        f"  {C(GREEN+BOLD, 'Threaded pipeline started')}  "
        f"{C(GRAY, f'capture={CAPTURE_FPS}fps  infer={INFER_FPS}fps  resize={INFER_W}px  q={JPEG_QUALITY}')}"
    )
    hr()
    print()
    print(
        f"  {C(GRAY,'TIME     ')}  "
        f"{C(GRAY,'cap=capFPS inf=inferFPS skip=dropped')}  "
        f"{C(GRAY,'SEVERITY')}  "
        f"{C(GRAY,'PERSONS UNK')}  "
        f"{C(GRAY,'MS')}  "
        f"{C(GRAY,'DETECTIONS')}"
    )
    hr("·")
    print()

    # Launch threads
    t_cap   = threading.Thread(target=_capture_loop, args=(cap,), daemon=True, name="capture")
    t_infer = threading.Thread(target=_infer_loop,               daemon=True, name="infer")
    t_print = threading.Thread(target=_print_loop,               daemon=True, name="print")

    t_cap.start()
    t_infer.start()
    t_print.start()

    # Keep main thread alive — exit on Ctrl+C
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        _stop_event.set()
        t_cap.join(timeout=3)
        t_infer.join(timeout=3)
        t_print.join(timeout=2)

# ─── Entry point ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # ── Parse CLI arguments (passed by start_ps.ps1) ──────────────────────
    parser = argparse.ArgumentParser(description="SENTINEL Live Capture Pipeline")
    parser.add_argument("--ip",    type=str, help="Phone camera IP address")
    parser.add_argument("--port",  type=int, default=8080, help="Phone camera port (default 8080)")
    parser.add_argument("--local", action="store_true", help="Use local webcam instead of phone")
    parser.add_argument("--index", type=int, default=0, help="Local webcam index (default 0)")
    args = parser.parse_args()

    # Apply CLI args to globals if provided, skipping the interactive prompt
    cli_provided = args.ip or args.local
    if args.local:
        CAMERA_SOURCE   = "local"
        LOCAL_CAM_INDEX = args.index
    elif args.ip:
        CAMERA_SOURCE = "phone"
        PHONE_IP      = args.ip
        PHONE_PORT    = args.port

    print_banner()

    # Only ask interactively if no CLI args were given
    if not cli_provided:
        ask_camera_config()
    else:
        # Show what was received from the launcher
        print(C(AMBER, "  CAMERA CONFIG  (from launcher)"))
        hr()
        if CAMERA_SOURCE == "phone":
            ok(f"Phone camera  {C(AMBER, PHONE_IP)}:{PHONE_PORT}")
        else:
            ok(f"Local webcam  index={LOCAL_CAM_INDEX}")
        print()
        hr()
        print()

    # Load known faces
    n = refresh_known_faces()
    ok(f"Known faces loaded: {C(CYAN, str(n))}")
    print()
    hr()
    print()

    # Block until YOLO is healthy
    if not wait_for_yolo():
        sys.exit(1)

    hr()
    print()

    try:
        run_capture()
    except KeyboardInterrupt:
        print()
        print()
        warn("Stopped by user  (Ctrl+C)")
        hr()
        print()
        sys.exit(0)