# SENTINEL · GreenWatch

> AI-powered edge surveillance system for detecting illegal artisanal gold mining (Galamsey) and monitoring environmental water quality — built for the **ACity Tech Expo 2026**.

![Python](https://img.shields.io/badge/Python-3.13-3776AB?style=flat-square&logo=python&logoColor=white)
![YOLOv8](https://img.shields.io/badge/YOLOv8-Ultralytics-FF6600?style=flat-square)
![FastAPI](https://img.shields.io/badge/FastAPI-0.110-009688?style=flat-square&logo=fastapi&logoColor=white)
![OpenCV](https://img.shields.io/badge/OpenCV-4.x-5C3EE8?style=flat-square&logo=opencv&logoColor=white)

**Theme alignment:** SDG 6 (Clean Water) · SDG 13 (Climate Action) · SDG 14 (Life Below Water) · SDG 15 (Life on Land)

---

## Overview

SENTINEL // GreenWatch is a real-time edge AI platform that points a camera at mining sites and water bodies to:

1. **Detect galamsey equipment** — excavators, mining trucks, dredgers, illegal pits — using a custom-trained YOLOv8 model
2. **Analyse water turbidity** — HSV colour decomposition detects sediment, mercury indicators, algae bloom, and dark stagnant pools
3. **Generate AI anomaly reports** — Gemma LLM produces structured incident reports flagging threats by severity
4. **Stream everything live** — WebSocket push to the SENTINEL dashboard, MJPEG video feed, EPA-ready incident logs

---

## Features

- **Custom YOLO model** (`galamsey.pt`) — trained on 700+ images of excavators and mining equipment via Google Colab T4
- **Water turbidity engine** — pure OpenCV HSV analysis, no external API, runs on CPU
- **Gemma AI reporting** — local LLM (via Ollama) generates structured `{severity, type, description, recommended_action}` anomaly flags
- **Temporal smoothing** — detections must appear in 3 of 5 consecutive frames before being reported (eliminates flicker)
- **MJPEG stream** — annotated frames served at `/stream` for live dashboard display
- **WebSocket broadcast** — every inference result pushed instantly to all connected dashboards
- **Threaded capture** — camera capture thread and inference thread run independently so Gemma latency never stalls the feed
- **Interactive launcher** — PowerShell and CMD scripts with health polling, ordered startup, and phone IP prompt

---

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Inference server | FastAPI + Uvicorn |
| Object detection | YOLOv8 (Ultralytics) — `galamsey.pt` |
| Water analysis | OpenCV HSV decomposition |
| AI reporting | Gemma 3 via Ollama (`gemma3-security` modelfile) |
| Camera pipeline | OpenCV + IP Webcam (Android) |
| DB logger | Flask + SQLAlchemy + SQLite |
| Dashboard | Standalone HTML/JS (no framework) |
| Video stream | MJPEG via FastAPI `StreamingResponse` |

---

## Project Structure

```
FaceNet/
├── yolo_server.py          # FastAPI inference server (YOLO + Gemma + turbidity + WS)
├── live_capture.py         # Threaded camera pipeline (phone or local webcam)
├── flask_logger.py         # Flask SQLAlchemy anomaly logger (port 5000)
├── lobe_plugin.py          # Flask plugin routes for external integrations
├── galamsey.pt             # Custom trained YOLOv8 model weights
├── galamsey_train.py       # Training script (run on Google Colab)
├── galamsey_colab.ipynb    # Colab training notebook
├── dashboard.html          # SENTINEL ops dashboard (standalone, no server needed)
├── sentinel-favicon.svg    # Browser tab icon
├── inference_log.ndjson    # Rolling NDJSON inference log
├── start_ps.ps1            # PowerShell full-stack launcher
├── start-sentinel.bat      # CMD full-stack launcher
└── Modelfile-gemma         # Ollama Gemma security modelfile (in F:\)
```

---

## Prerequisites

**Python packages:**
```bash
pip install fastapi uvicorn[standard] ultralytics opencv-python \
            requests pillow numpy httpx flask flask-sqlalchemy \
            flask-cors psutil
```

**Other requirements:**
- [Ollama](https://ollama.com) installed and running
- `gemma3-security` model created from `F:\Modelfile-gemma`
- Android phone with [IP Webcam](https://play.google.com/store/apps/details?id=com.pas.webcam) (for phone camera mode)

---

## Setup

### 1. Create the Gemma security model

```bash
ollama create gemma3-security -f F:\Modelfile-gemma
```

### 2. Place the model file

Ensure `galamsey.pt` is at `F:\FaceNet\galamsey.pt`. This is the custom-trained model — do not replace it with a generic YOLO model.

### 3. Launch everything

**PowerShell (recommended):**
```powershell
cd F:\FaceNet
powershell -ExecutionPolicy Bypass -File .\start_ps.ps1
```

**CMD:**
```bat
cd F:\FaceNet
start-sentinel.bat
```

The launcher will:
1. Ask whether to use a phone camera or local webcam
2. Prompt for the phone IP address (if phone mode)
3. Start Flask logger → YOLO server → Node API → React UI → Live capture
4. Wait for YOLO to finish loading (polls `/health` with a progress bar) before starting live capture

---

## Running Services Manually

```powershell
# Terminal 1 — Flask DB logger
cd F:\FaceNet
python flask_logger.py

# Terminal 2 — YOLO inference server
cd F:\FaceNet
uvicorn yolo_server:app --host 0.0.0.0 --port 8000

# Terminal 3 — Live capture (with phone camera)
cd F:\FaceNet
python live_capture.py --ip 192.168.1.45 --port 8080

# Terminal 4 — Live capture (local webcam)
cd F:\FaceNet
python live_capture.py --local --index 0
```

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Server health — returns model names and client count |
| `POST` | `/infer` | Submit a base64 JPEG frame for inference |
| `GET` | `/stream` | MJPEG annotated video stream |
| `GET` | `/snapshot` | Single annotated JPEG frame |
| `GET` | `/logs` | Recent inference log records |
| `WS` | `/ws` | WebSocket — real-time inference result push |

### `/infer` request body

```json
{
  "frame_b64": "<base64 JPEG>",
  "device_id": "DESKTOP-ABC",
  "session_id": "a1b2c3d4e5f6",
  "timestamp": 1710000000.0,
  "known_faces": [{ "id": "Trinity", "name": "Trinity", "confidence": 0.95 }],
  "location": "Surveillance Zone A"
}
```

### WebSocket message format

```json
{
  "type": "inference_result",
  "payload": {
    "person_count": 2,
    "unknown_count": 1,
    "detections": [{ "label": "excavator", "confidence": 0.87, "bbox": [x1,y1,x2,y2] }],
    "anomalies": [{ "severity": "high", "type": "unauthorized_entry", "description": "...", "recommended_action": "..." }],
    "turbidity": { "score": 72, "level": "CRITICAL", "color_signature": "sediment", "threat_flags": ["SEDIMENT_CONTAMINATION", "EPA_THRESHOLD_EXCEEDED"], "metrics": {...} },
    "max_severity": "high",
    "processing_ms": 347.2,
    "turbidity_level": "CRITICAL",
    "turbidity_score": 72
  }
}
```

---

## Water Turbidity Engine

The turbidity module (`_analyze_turbidity` inside `yolo_server.py`) analyses every frame using OpenCV HSV colour decomposition. No external API is required.

### Signatures detected

| Signature | HSV Range | Galamsey Link |
|-----------|-----------|---------------|
| Sediment | Hue 6–28°, Sat > 55 | Brown/orange mining runoff |
| Mercury | Sat < 45, Val > 110 | Milky grey from amalgamation |
| Algae | Hue 34–82°, Sat > 55 | Chemical/nutrient runoff |
| Dark pool | Val < 65 | Stagnant mine pit water |

### Score levels

| Range | Level | Anomaly severity |
|-------|-------|-----------------|
| 0–20 | CLEAR | None |
| 21–45 | MODERATE | Low |
| 46–70 | TURBID | Medium |
| 71–100 | CRITICAL | High + EPA flag |

---

## Training the YOLO Model

The model was trained on Google Colab (T4 GPU) using the Roboflow EXCAVATOR.v1i dataset (732 images, 100 epochs, 640px input). Local training is **not recommended** on the i5 8th Gen / MX130 hardware — use Colab.

```python
# galamsey_train.py  (run on Colab)
from ultralytics import YOLO
model = YOLO("yolov8s.pt")
model.train(data="data.yaml", epochs=100, imgsz=640, device="cuda")
```

Download `best.pt` from `runs/detect/train/weights/best.pt` and rename to `galamsey.pt`.

---

## Dashboard

Open `F:\FaceNet\dashboard.html` directly in a browser — no server required.

The dashboard has 5 tabs:

| Tab | Content |
|-----|---------|
| **Overview** | Live MJPEG feed, person count chart, camera metadata |
| **Events** | Filterable anomaly event feed (HIGH / MED / LOW / WATER / INFO) |
| **Detections** | Per-frame YOLO object cards with confidence bars |
| **Water Quality** | Turbidity score, 4 metric cards, threat flags, score history sparkline |
| **System Log** | Full inference terminal |

---

## Performance (i5 8th Gen + MX130)

| Setting | Value |
|---------|-------|
| Capture FPS | ~15 (phone HTTP) / ~30 (local webcam) |
| Inference FPS | 4–8 (depends on Gemma latency) |
| YOLO inference | ~80–150ms |
| Gemma response | 1–3s (runs async, doesn't block capture) |
| Resize before encode | 640px wide (cuts payload ~75%) |

---

## Port Reference

| Service | Port |
|---------|------|
| YOLO inference server | `8000` |
| Flask DB logger | `5000` |
| Node API (FaceNet-Node) | `3001` |
| React UI (FaceNet-Node) | `5173` |
| IP Webcam (phone) | `8080` |

---

## License

MIT — Academic City University, Ghana · ACity Tech Expo 2026  
Theme: *Tech Expo @5: Innovating to Reverse the Effects of Galamsey and Restore the Earth*
