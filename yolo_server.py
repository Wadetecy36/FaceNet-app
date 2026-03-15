"""
yolo_server.py (v4 — GreenWatch + Turbidity)
=============================================
YOLO + Gemma + OpenCV Water Turbidity inference server with:
  - Auto-forwarding results to Flask SQLAlchemy logger
  - WebSocket broadcast for live dashboard push
  - Water turbidity analysis via HSV colour decomposition
  - Reconnect-safe camera handling

Start:
    uvicorn yolo_server:app --host 0.0.0.0 --port 8000 --reload
"""

import base64
import json
import re
import time
import logging
import asyncio
from collections import deque, defaultdict
from datetime import datetime
from typing import Optional

import cv2
import numpy as np
import requests
import httpx
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from ultralytics import YOLO

# ─── Config ───────────────────────────────────────────────────────────────────

OLLAMA_HOST = "http://localhost:11434"
GEMMA_MODEL = "gemma3-security"
YOLO_MODEL  = "galamsey.pt"
LOG_FILE    = "F:/FaceNet/inference_log.ndjson"

# Flask logger endpoint — set to None to disable
FLASK_URL   = "http://localhost:5000/api/anomalies"

# ─── Temporal smoothing ───────────────────────────────────────────────────────

BUFFER_SIZE       = 5     # rolling frame window
CONFIRM_THRESHOLD = 3     # must appear in 3/5 frames to be confirmed
CONF_MIN          = 0.45  # minimum YOLO confidence

# ─── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level   = logging.INFO,
    format  = "%(asctime)s [%(levelname)s] %(message)s",
    datefmt = "%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("yolo_server")

# ─── Load YOLO ────────────────────────────────────────────────────────────────

log.info(f"Loading YOLO model: {YOLO_MODEL}")
yolo = YOLO(YOLO_MODEL)
log.info("YOLO ready ✓")

# ─── Temporal smoothing buffer ────────────────────────────────────────────────

_detection_buffer: dict[str, deque] = defaultdict(lambda: deque(maxlen=BUFFER_SIZE))


def smooth_detections(session_id: str, raw: list) -> list:
    """
    Filter detections via rolling frame buffer.
    Only returns labels seen in >= CONFIRM_THRESHOLD of last BUFFER_SIZE frames.
    """
    this_frame: dict[str, object] = {}
    for det in raw:
        if det.confidence < CONF_MIN:
            continue
        label = det.label
        if label not in this_frame or det.confidence > this_frame[label].confidence:
            this_frame[label] = det

    _detection_buffer[session_id].append(set(this_frame.keys()))

    buf = _detection_buffer[session_id]
    label_counts: dict[str, int] = defaultdict(int)
    for frame_labels in buf:
        for lbl in frame_labels:
            label_counts[lbl] += 1

    confirmed = [
        this_frame[lbl]
        for lbl, count in label_counts.items()
        if count >= CONFIRM_THRESHOLD and lbl in this_frame
    ]
    return confirmed


# ─── FastAPI ──────────────────────────────────────────────────────────────────

app = FastAPI(title="GreenWatch Inference Server", version="4.0.0", docs_url="/docs")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# ─── WebSocket manager ────────────────────────────────────────────────────────

class ConnectionManager:
    def __init__(self):
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)
        log.info(f"Dashboard connected ({len(self.active)} clients)")

    def disconnect(self, ws: WebSocket):
        if ws in self.active:
            self.active.remove(ws)
        log.info(f"Dashboard disconnected ({len(self.active)} clients)")

    async def broadcast(self, data: dict):
        if not self.active:
            return
        msg  = json.dumps(data)
        dead = []
        for ws in self.active:
            try:
                await ws.send_text(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            if ws in self.active:
                self.active.remove(ws)


ws_manager = ConnectionManager()

# ─── Live frame store ─────────────────────────────────────────────────────────
# Holds the latest annotated JPEG bytes for MJPEG streaming to the dashboard.
# Written by the /infer endpoint; read by the /stream endpoint.

import threading as _threading
_frame_lock   = _threading.Lock()
_latest_frame: bytes = b""          # raw JPEG bytes of last annotated frame
_frame_event  = _threading.Event()  # signals that a new frame is available

STREAM_FPS    = 10    # max frames per second served to dashboard
STREAM_W      = 640   # resize width before streaming (saves bandwidth)

# ── Annotation colours ────────────────────────────────────────────────────────
_TURB_BGR = {
    "CLEAR":    (0,   230, 118),
    "MODERATE": (0,   179, 255),
    "TURBID":   (30,  124, 255),
    "CRITICAL": (59,   59, 255),
}
_DET_BGR  = (0, 230, 118)    # green for all YOLO boxes


def _annotate_frame(frame_bgr: np.ndarray,
                    detections: list,
                    turbidity: dict | None) -> bytes:
    """
    Draw YOLO bounding boxes + turbidity HUD onto a copy of the frame.
    Returns JPEG bytes ready to stream.
    """
    out = frame_bgr.copy()
    h, w = out.shape[:2]

    # Resize to stream width to save bandwidth
    if w > STREAM_W:
        scale = STREAM_W / w
        out   = cv2.resize(out, (STREAM_W, int(h * scale)), interpolation=cv2.INTER_AREA)
        scale_x = STREAM_W / w
        scale_y = scale
    else:
        scale_x = scale_y = 1.0

    # ── Draw YOLO detections ─────────────────────────────────────────────────
    for det in detections:
        if not hasattr(det, "bbox") or len(det.bbox) < 4:
            continue
        x1 = int(det.bbox[0] * scale_x)
        y1 = int(det.bbox[1] * scale_y)
        x2 = int(det.bbox[2] * scale_x)
        y2 = int(det.bbox[3] * scale_y)

        cv2.rectangle(out, (x1, y1), (x2, y2), _DET_BGR, 2)

        # Corner brackets
        cl = 14
        for (cx, cy, dx, dy) in [
            (x1, y1,  cl, cl), (x2, y1, -cl,  cl),
            (x1, y2,  cl,-cl), (x2, y2, -cl, -cl),
        ]:
            cv2.line(out, (cx, cy), (cx + dx, cy), _DET_BGR, 2)
            cv2.line(out, (cx, cy), (cx, cy + dy), _DET_BGR, 2)

        # Label plate
        label   = f"{det.label}  {int(det.confidence * 100)}%"
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.40, 1)
        cv2.rectangle(out, (x1, y1 - th - 8), (x1 + tw + 8, y1), _DET_BGR, -1)
        cv2.putText(out, label, (x1 + 4, y1 - 4),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.40, (10, 20, 10), 1, cv2.LINE_AA)

    # ── Turbidity HUD (top-left panel) ───────────────────────────────────────
    if turbidity:
        level  = turbidity.get("level", "CLEAR")
        score  = turbidity.get("score", 0)
        sig    = turbidity.get("color_signature", "clear")
        col    = _TURB_BGR.get(level, (100, 100, 100))

        # Background box
        cv2.rectangle(out, (6, 6), (230, 78), (10, 16, 22), -1)
        cv2.rectangle(out, (6, 6), (230, 78), col, 1)

        # Title
        cv2.putText(out, "WATER QUALITY", (12, 24),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.38, (100, 140, 170), 1, cv2.LINE_AA)

        # Level badge
        cv2.rectangle(out, (148, 10), (224, 28), col, -1)
        cv2.putText(out, level, (152, 24),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.38, (10, 16, 22), 1, cv2.LINE_AA)

        # Score bar
        bx, by, bw, bh = 12, 34, 212, 8
        cv2.rectangle(out, (bx, by), (bx + bw, by + bh), (30, 42, 55), -1)
        filled = int(bw * score / 100)
        cv2.rectangle(out, (bx, by), (bx + filled, by + bh), col, -1)
        cv2.putText(out, f"{score}/100  {sig}", (bx, by + bh + 14),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.33, (120, 150, 170), 1, cv2.LINE_AA)

        # Threat flags
        flags = turbidity.get("threat_flags", [])
        if flags:
            flag_txt = "  ".join(f[:14] for f in flags[:3])
            cv2.putText(out, flag_txt, (12, 72),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.28, col, 1, cv2.LINE_AA)

    # ── Encode to JPEG ────────────────────────────────────────────────────────
    ok_flag, buf = cv2.imencode(".jpg", out, [cv2.IMWRITE_JPEG_QUALITY, 70])
    if not ok_flag:
        return b""
    return buf.tobytes()


def _store_frame(jpeg_bytes: bytes) -> None:
    global _latest_frame
    with _frame_lock:
        _latest_frame = jpeg_bytes
    _frame_event.set()
    _frame_event.clear()

# ─── Pydantic models ──────────────────────────────────────────────────────────

class KnownFace(BaseModel):
    id:         str
    name:       str
    confidence: float

class FrameRequest(BaseModel):
    frame_b64:   str
    device_id:   str
    session_id:  str
    timestamp:   float
    known_faces: list[KnownFace] = []
    location:    Optional[str]   = ""

class Detection(BaseModel):
    label:      str
    confidence: float
    bbox:       list[int]

class AnomalyFlag(BaseModel):
    severity:           str
    type:               str
    description:        str
    recommended_action: str

class InferenceResponse(BaseModel):
    device_id:     str
    session_id:    str
    timestamp:     float
    iso_time:      str
    location:      str
    detections:    list[Detection]
    person_count:  int
    known_count:   int
    unknown_count: int
    anomalies:     list[AnomalyFlag]
    log_entry:     str
    processing_ms: float
    turbidity:     Optional[dict] = None


# ═══════════════════════════════════════════════════════════════════════════════
# WATER TURBIDITY ENGINE  (OpenCV HSV)
# ═══════════════════════════════════════════════════════════════════════════════
#
# Four galamsey contamination signatures detected via HSV decomposition:
#
#   SEDIMENT  — brown/orange mining runoff   hue 6-28°,  sat>55
#   MERCURY   — milky grey amalgamation      sat<45,     val>110
#   ALGAE     — green nutrient/chem bloom    hue 34-82°, sat>55
#   DARK_POOL — stagnant mine pit water      val<65
#
# Score 0-100:
#   0-20  CLEAR     ✅
#   21-45 MODERATE  ⚠
#   46-70 TURBID    🟠  → medium anomaly
#   71+   CRITICAL  🔴  → high anomaly + EPA flag
# ═══════════════════════════════════════════════════════════════════════════════

_SED_H_LO,  _SED_H_HI  =  6,  28
_SED_S_MIN              = 55
_SED_V_MIN, _SED_V_MAX  = 40, 210

_MER_S_MAX              = 45
_MER_V_MIN              = 110

_ALG_H_LO,  _ALG_H_HI  = 34,  82
_ALG_S_MIN              = 55
_ALG_V_MIN              = 50

_DARK_V_MAX             = 65

_W_SED  = 38
_W_MER  = 22
_W_DARK = 18
_W_UNIF = 12
_W_ALG  = 10

_LVL_MOD  = 21
_LVL_TURB = 46
_LVL_CRIT = 71
_MIN_PCT  =  2.0


def _analyze_turbidity(frame_bgr: np.ndarray) -> dict:
    """Run HSV turbidity analysis on a BGR frame. Returns a JSON-ready dict."""
    if frame_bgr is None or frame_bgr.size == 0:
        return _turb_zero()

    # Resize to 320px wide for consistent, fast analysis
    h_orig, w_orig = frame_bgr.shape[:2]
    new_h = max(1, int(h_orig * 320 / w_orig))
    small = cv2.resize(frame_bgr, (320, new_h), interpolation=cv2.INTER_AREA)

    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV).astype(np.float32)
    H, S, V = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    total   = max(float(H.size), 1.0)

    def pct(mask):
        return float(np.sum(mask)) / total * 100.0

    # Build masks
    sed_mask  = (H >= _SED_H_LO) & (H <= _SED_H_HI) & (S >= _SED_S_MIN) & (V >= _SED_V_MIN) & (V <= _SED_V_MAX)
    mer_mask  = (S <= _MER_S_MAX) & (V >= _MER_V_MIN)
    alg_mask  = (H >= _ALG_H_LO) & (H <= _ALG_H_HI) & (S >= _ALG_S_MIN) & (V >= _ALG_V_MIN)
    dark_mask = (V <= _DARK_V_MAX)

    brown_pct   = pct(sed_mask)
    mercury_pct = pct(mer_mask)
    algae_pct   = pct(alg_mask)
    dark_pct    = pct(dark_mask)

    mean_v     = float(np.mean(V))
    uniformity = float(np.clip(1.0 - (float(np.std(V)) / 90.0), 0.0, 1.0))

    non_dark = ~dark_mask
    dom_hue  = float(np.mean(H[non_dark])) if np.any(non_dark) else 0.0

    # Weighted score
    score = int(np.clip(round(
        min(brown_pct   / 60.0, 1.0) * 100 * (_W_SED  / 100) +
        min(mercury_pct / 50.0, 1.0) * 100 * (_W_MER  / 100) +
        min(dark_pct    / 50.0, 1.0) * 100 * (_W_DARK / 100) +
        uniformity                   * 100 * (_W_UNIF / 100) +
        min(algae_pct   / 30.0, 1.0) * 100 * (_W_ALG  / 100)
    ), 0, 100))

    level = (
        "CRITICAL" if score >= _LVL_CRIT else
        "TURBID"   if score >= _LVL_TURB else
        "MODERATE" if score >= _LVL_MOD  else
        "CLEAR"
    )

    # Dominant signature
    sigs = {
        "sediment":  brown_pct   * 1.0,
        "mercury":   mercury_pct * 0.8,
        "algae":     algae_pct   * 0.9,
        "dark_pool": dark_pct    * 0.7,
    }
    dominant  = max(sigs, key=sigs.get)
    color_sig = dominant if sigs[dominant] >= _MIN_PCT else "clear"

    # Threat flags
    flags = []
    if brown_pct   >= _MIN_PCT:     flags.append("SEDIMENT_CONTAMINATION")
    if mercury_pct >= _MIN_PCT * 2: flags.append("MERCURY_INDICATOR")
    if algae_pct   >= _MIN_PCT:     flags.append("ALGAE_BLOOM")
    if dark_pct    >= 30:           flags.append("DARK_POOL_DETECTED")
    if uniformity  >= 0.80:         flags.append("HIGH_UNIFORMITY_WATER")
    if score       >= _LVL_CRIT:   flags.append("EPA_THRESHOLD_EXCEEDED")

    return {
        "score":           score,
        "level":           level,
        "color_signature": color_sig,
        "threat_flags":    flags,
        "metrics": {
            "brown_pct":       round(brown_pct,    2),
            "mercury_pct":     round(mercury_pct,  2),
            "algae_pct":       round(algae_pct,    2),
            "dark_pct":        round(dark_pct,     2),
            "mean_brightness": round(mean_v,       2),
            "uniformity":      round(uniformity,   3),
            "dominant_hue":    round(dom_hue,      1),
            "pixel_count":     int(total),
        },
    }


def _turb_zero() -> dict:
    return {
        "score": 0, "level": "CLEAR", "color_signature": "clear",
        "threat_flags": [],
        "metrics": {
            "brown_pct": 0, "mercury_pct": 0, "algae_pct": 0, "dark_pct": 0,
            "mean_brightness": 255, "uniformity": 0, "dominant_hue": 0, "pixel_count": 0,
        },
    }


def _turb_to_anomaly(turb: dict) -> Optional[dict]:
    """Convert turbidity result to an AnomalyFlag dict, or None if CLEAR."""
    level = turb.get("level", "CLEAR")
    if level == "CLEAR":
        return None

    sig_labels = {
        "sediment":  "Suspended sediment (mining runoff)",
        "mercury":   "Milky/grey colouration — possible mercury contamination",
        "algae":     "Green algae bloom — nutrient/chemical runoff",
        "dark_pool": "Dark stagnant water — mine pit contamination",
        "clear":     "Mild colour deviation detected",
    }
    actions = {
        "CRITICAL": "Isolate water source immediately. Alert EPA. Do not consume.",
        "TURBID":   "Collect water sample for lab analysis. Log GPS coordinates.",
        "MODERATE": "Continue monitoring. Flag for weekly inspection report.",
    }
    sev_map = {"CRITICAL": "high", "TURBID": "medium", "MODERATE": "low"}

    sig   = turb.get("color_signature", "clear")
    score = turb.get("score", 0)
    flags = turb.get("threat_flags", [])

    return {
        "severity":           sev_map.get(level, "low"),
        "type":               f"water_quality_{level.lower()}",
        "description":        (
            f"{sig_labels.get(sig, 'Contamination detected')}. "
            f"Turbidity score: {score}/100. "
            f"Flags: {', '.join(flags) if flags else 'none'}."
        ),
        "recommended_action": actions.get(level, "Log and monitor."),
    }


# ─── JSON cleaner ─────────────────────────────────────────────────────────────

def clean_llm_json(raw: str) -> str:
    if "```" in raw:
        parts = raw.split("```")
        for part in parts:
            part = part.strip()
            if part.startswith("json"):
                part = part[4:]
            if part.strip().startswith("{"):
                raw = part
                break

    def collapse_string(match: re.Match) -> str:
        inner = match.group(0)
        inner = inner.replace("\n", " ").replace("\r", " ")
        return re.sub(r"  +", " ", inner)

    return re.sub(r'"[^"]*"', collapse_string, raw).strip()


# ─── YOLO runner ──────────────────────────────────────────────────────────────

def run_yolo(frame_bgr: np.ndarray) -> list[Detection]:
    results    = yolo(frame_bgr, verbose=False, conf=CONF_MIN)[0]
    detections = []
    for box in results.boxes:
        label = yolo.names[int(box.cls[0])]
        conf  = float(box.conf[0])
        x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
        detections.append(Detection(
            label=label, confidence=round(conf, 3), bbox=[x1, y1, x2, y2]
        ))
    return detections


# ─── Gemma ────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = (
    "You are a security AI. Given camera detection data, output a JSON anomaly report. "
    "Fill in real values — do NOT copy the field names as values. "
    "Output ONLY raw JSON, no markdown, no backticks, no extra text. "
    "Example output: "
    '{"anomalies": [{"severity": "high", "type": "unauthorized_entry", '
    '"description": "Unknown person entered restricted area.", '
    '"recommended_action": "Alert security personnel immediately."}], '
    '"log_entry": "2025-03-10 09:46:40 - 4 persons at Main Entrance, 4 unidentified."}'
)


def query_gemma(detections, known_faces, person_count, location, timestamp):
    dt            = datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
    unknown_count = person_count - len(known_faces)

    user_msg = (
        f"Timestamp: {dt}\n"
        f"Location: {location or 'Unknown'}\n"
        f"Total persons: {person_count}\n"
        f"Identified: {len(known_faces)}\n"
        f"Unidentified: {unknown_count}\n\n"
        f"Known faces: {json.dumps([f.dict() for f in known_faces]) if known_faces else 'None'}\n\n"
        f"YOLO detections: {json.dumps([d.dict() for d in detections])}\n\n"
        "Return JSON anomaly report now."
    )

    try:
        resp = requests.post(
            f"{OLLAMA_HOST}/api/chat",
            json={
                "model":  GEMMA_MODEL,
                "stream": False,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user",   "content": user_msg},
                ],
                "options": {
                    "temperature": 0.1, "top_p": 0.9,
                    "repeat_penalty": 1.1, "num_predict": 256,
                },
            },
            timeout=20,
        )
        resp.raise_for_status()
        raw    = resp.json()["message"]["content"]
        parsed = json.loads(clean_llm_json(raw))
        anomalies = [AnomalyFlag(**a) for a in parsed.get("anomalies", [])]
        log_entry = parsed.get("log_entry", f"[{dt}] {person_count} person(s) at {location}.")
        return anomalies, log_entry

    except json.JSONDecodeError as e:
        log.warning(f"Gemma JSON parse failed: {e}")
        return [], f"[{dt}] {person_count} person(s) at {location or 'Unknown'}. (Parse error)"
    except Exception as e:
        log.error(f"Gemma error: {type(e).__name__}: {e}")
        return [], f"[{dt}] {person_count} person(s) at {location or 'Unknown'}. (Gemma unavailable)"


# ─── Disk logger ──────────────────────────────────────────────────────────────

def append_log(record: dict) -> None:
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except Exception as e:
        log.error(f"Log write failed: {e}")


# ─── Flask forwarder ──────────────────────────────────────────────────────────

async def forward_to_flask(record: dict) -> None:
    if not FLASK_URL:
        return
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            await client.post(FLASK_URL, json=record)
    except Exception as e:
        log.warning(f"Flask forward failed: {type(e).__name__}: {e}")


# ─── Routes ───────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {
        "status":  "ok",
        "yolo":    YOLO_MODEL,
        "llm":     GEMMA_MODEL,
        "clients": len(ws_manager.active),
    }


@app.get("/snapshot")
def snapshot():
    """Return the latest annotated frame as a single JPEG image."""
    with _frame_lock:
        data = _latest_frame
    if not data:
        # Return a 1x1 black pixel if no frame yet
        blank = np.zeros((240, 320, 3), dtype=np.uint8)
        cv2.putText(blank, "Awaiting feed...", (60, 125),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (60, 90, 120), 1)
        _, buf = cv2.imencode(".jpg", blank)
        data   = buf.tobytes()
    return StreamingResponse(
        iter([data]),
        media_type="image/jpeg",
        headers={"Cache-Control": "no-cache, no-store"},
    )


@app.get("/stream")
async def stream():
    """
    MJPEG stream of annotated inference frames.
    Dashboard uses: <img src="http://localhost:8000/stream">
    """
    async def generate():
        _interval = 1.0 / STREAM_FPS
        _blank    = None

        while True:
            with _frame_lock:
                data = _latest_frame

            if data:
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n\r\n" +
                    data +
                    b"\r\n"
                )
            else:
                # Send a placeholder until first real frame arrives
                if _blank is None:
                    blank = np.zeros((240, 320, 3), dtype=np.uint8)
                    cv2.putText(blank, "Awaiting feed...", (60, 125),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                                (60, 90, 120), 1, cv2.LINE_AA)
                    _, buf = cv2.imencode(".jpg", blank)
                    _blank = buf.tobytes()
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n\r\n" +
                    _blank +
                    b"\r\n"
                )

            await asyncio.sleep(_interval)

    return StreamingResponse(
        generate(),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-cache"},
    )


@app.get("/logs")
def get_logs(limit: int = 50, anomalies_only: bool = False):
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except FileNotFoundError:
        return {"count": 0, "records": []}
    records = [json.loads(l) for l in lines if l.strip()]
    records.reverse()
    if anomalies_only:
        records = [r for r in records if r.get("has_anomaly")]
    return {"count": len(records), "records": records[:limit]}


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    """Dashboard connects here for live push updates."""
    await ws_manager.connect(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(ws)


@app.post("/infer", response_model=InferenceResponse)
async def infer(req: FrameRequest):
    t_start = time.perf_counter()

    # ── Decode frame ──────────────────────────────────────────────────────────
    try:
        img_bytes = base64.b64decode(req.frame_b64)
        np_arr    = np.frombuffer(img_bytes, np.uint8)
        frame_bgr = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
        if frame_bgr is None:
            raise ValueError("Frame decoded to None")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid frame: {e}")

    # ── YOLO + temporal smoothing ─────────────────────────────────────────────
    raw_detections = run_yolo(frame_bgr)
    detections     = smooth_detections(req.session_id, raw_detections)
    person_count   = sum(1 for d in detections if d.label == "person")
    unknown_count  = person_count - len(req.known_faces)

    # ── Gemma anomaly analysis ────────────────────────────────────────────────
    anomalies, log_entry = query_gemma(
        detections=detections, known_faces=req.known_faces,
        person_count=person_count, location=req.location or "",
        timestamp=req.timestamp,
    )

    # ── Water turbidity (HSV) ─────────────────────────────────────────────────
    turbidity_dict = None
    try:
        turbidity_dict = _analyze_turbidity(frame_bgr)
        turb_anomaly   = _turb_to_anomaly(turbidity_dict)
        if turb_anomaly:
            anomalies.append(AnomalyFlag(**turb_anomaly))
            log_entry = (
                log_entry.rstrip(".")
                + f" | Water: {turbidity_dict['level']} (score {turbidity_dict['score']}/100)."
            )
    except Exception as e:
        log.warning(f"Turbidity analysis failed: {e}")

    # ── Build response ────────────────────────────────────────────────────────
    processing_ms = round((time.perf_counter() - t_start) * 1000, 2)
    iso_time      = datetime.fromtimestamp(req.timestamp).isoformat()

    result = InferenceResponse(
        device_id=req.device_id, session_id=req.session_id,
        timestamp=req.timestamp, iso_time=iso_time, location=req.location or "",
        detections=detections, person_count=person_count,
        known_count=len(req.known_faces), unknown_count=unknown_count,
        anomalies=anomalies, log_entry=log_entry, processing_ms=processing_ms,
        turbidity=turbidity_dict,
    )

    # ── Log record ────────────────────────────────────────────────────────────
    log_record = result.dict()
    log_record["has_anomaly"]     = len(anomalies) > 0
    log_record["max_severity"]    = (
        max((a.severity for a in anomalies),
            key=lambda s: {"low": 1, "medium": 2, "high": 3}.get(s, 0))
        if anomalies else "none"
    )
    log_record["timestamp_ms"]    = int(req.timestamp * 1000)
    log_record["turbidity_score"] = turbidity_dict["score"] if turbidity_dict else None
    log_record["turbidity_level"] = turbidity_dict["level"] if turbidity_dict else "UNKNOWN"

    # ── Annotate frame + store for MJPEG stream ──────────────────────────────
    try:
        jpeg = _annotate_frame(frame_bgr, detections, turbidity_dict)
        _store_frame(jpeg)
    except Exception as e:
        log.warning(f"Frame annotation failed: {e}")

    # ── Persist + broadcast ───────────────────────────────────────────────────
    append_log(log_record)
    asyncio.create_task(forward_to_flask(log_record))
    asyncio.create_task(ws_manager.broadcast({
        "type":    "inference_result",
        "payload": log_record,
    }))

    # ── Console ───────────────────────────────────────────────────────────────
    for a in anomalies:
        if a.severity == "high":
            log.warning(f"\033[31m[HIGH ALERT]\033[0m {a.type}: {a.description}")

    turb_label = turbidity_dict["level"] if turbidity_dict else "?"
    log.info(
        f"{req.device_id} | {person_count} person(s) | "
        f"{len(anomalies)} anomaly(s) | water={turb_label} | {processing_ms}ms"
    )
    return result