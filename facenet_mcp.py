#!/usr/bin/env python3
# ============================================================
# facenet_mcp.py (v2 - FastMCP)
# MCP stdio server for FaceNet-Node — LobeHub desktop
# ============================================================

import json
import subprocess
import requests
import psutil
from datetime import datetime, timedelta
from mcp.server.fastmcp import FastMCP

# ─── Config ───────────────────────────────────────────────
FLASK_URL   = "http://localhost:5000"
YOLO_URL    = "http://localhost:8000"
FACENET_URL = "http://localhost:3001"
PYTHON      = r"C:\Users\Trinity\AppData\Local\Programs\Python\Python313\python.exe"
CAPTURE_PY  = r"F:\FaceNet\live_capture.py"

mcp = FastMCP("facenet-node-security")

@mcp.tool()
def query_anomalies(
    severity: str = "all",
    hours: int = 0,
    limit: int = 20,
    anomalies_only: bool = False
) -> str:
    """
    Query FaceNet-Node anomaly detection logs.
    Filter by severity (high/medium/low/all), time window in hours,
    and whether to show only frames with anomalies.
    Use this when asked about alerts, threats, detections, incidents, or person counts.
    """
    params = {
        "severity":       severity,
        "limit":          limit,
        "anomalies_only": str(anomalies_only).lower(),
    }
    if hours and hours > 0:
        params["hours"] = hours

    try:
        r = requests.get(f"{FLASK_URL}/plugin/anomalies", params=params, timeout=5)
        data = r.json()
    except Exception as e:
        return f"Cannot reach Flask logger: {e}\nMake sure flask_logger.py is running on port 5000."

    summary = data.get("summary", {})
    records = data.get("records", [])

    lines = [
        "Anomaly Log Summary",
        f"Total matched: {summary.get('total_records', 0)}",
        f"High: {summary.get('high_severity', 0)}",
        f"Medium: {summary.get('medium_severity', 0)}",
        f"Low: {summary.get('low_severity', 0)}",
        "",
    ]

    if not records:
        lines.append("No records found for the given filters.")
    else:
        lines.append(f"Last {len(records)} Records")
        for rec in records:
            lines.append(f"[{rec.get('severity','?').upper()}] {rec.get('time','?')} - {rec.get('location','?')}")
            lines.append(f"  Persons: {rec.get('person_count', 0)} detected, {rec.get('unknown_count', 0)} unknown")
            for a in rec.get("anomalies", []):
                lines.append(f"  {a}")
            if rec.get("log_entry"):
                lines.append(f"  Note: {rec['log_entry']}")

    return "\n".join(lines)


@mcp.tool()
def get_system_health() -> str:
    """
    Get the live health status of all FaceNet-Node services:
    Flask logger, YOLO inference server, FaceNet-Node API, and live capture.
    Use when asked if the system is running, online, or healthy.
    """
    services = {}

    try:
        requests.get(f"{FLASK_URL}/api/health", timeout=2)
        count = 0
        try:
            count = requests.get(f"{FLASK_URL}/api/stats", timeout=2).json().get("total_records", 0)
        except:
            pass
        services["flask_logger"] = {"status": "online", "port": 5000, "records": count}
    except:
        services["flask_logger"] = {"status": "OFFLINE", "port": 5000}

    try:
        d = requests.get(f"{YOLO_URL}/health", timeout=2).json()
        services["yolo_server"] = {
            "status": "online", "port": 8000,
            "model": d.get("yolo", "?"),
            "llm":   d.get("llm", "?"),
            "ws_clients": d.get("clients", 0)
        }
    except:
        services["yolo_server"] = {"status": "OFFLINE", "port": 8000}

    try:
        users = requests.get(f"{FACENET_URL}/api/users", timeout=2).json()
        services["facenet_api"] = {
            "status": "online", "port": 3001,
            "registered_users": len(users) if isinstance(users, list) else 0
        }
    except:
        services["facenet_api"] = {"status": "OFFLINE", "port": 3001}

    capture_pid = None
    for proc in psutil.process_iter(["pid", "cmdline"]):
        try:
            if "live_capture.py" in " ".join(proc.info.get("cmdline") or []):
                capture_pid = proc.info["pid"]
                break
        except:
            pass
    services["live_capture"] = {
        "status": f"running (PID {capture_pid})" if capture_pid else "stopped"
    }

    online = sum(1 for s in services.values() if any(x in s["status"] for x in ("online", "running")))
    total  = len(services)

    lines = [
        f"FaceNet-Node Health - {online}/{total} online",
        f"Checked: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
    ]

    for svc, info in services.items():
        st   = info["status"]
        icon = "OK" if any(x in st for x in ("online","running")) else ("WARN" if "stopped" in st else "OFFLINE")
        lines.append(f"[{icon}] {svc} - {st}")
        for k, v in info.items():
            if k != "status":
                lines.append(f"   {k}: {v}")

    if online < total:
        lines.append("\nSome services offline - run start-facenet.ps1 to restart.")

    return "\n".join(lines)


@mcp.tool()
def control_capture(action: str) -> str:
    """
    Start or stop the FaceNet-Node live capture pipeline.
    action must be 'start' or 'stop'.
    Use when asked to start or stop monitoring, the camera, or surveillance.
    """
    action = action.lower().strip()

    if action == "start":
        for proc in psutil.process_iter(["pid", "cmdline"]):
            try:
                if "live_capture.py" in " ".join(proc.info.get("cmdline") or []):
                    return f"Live capture is already running (PID {proc.info['pid']})."
            except:
                pass
        proc = subprocess.Popen(
            [PYTHON, CAPTURE_PY],
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )
        return f"Live capture started (PID {proc.pid}). Camera feed is now being processed."

    elif action == "stop":
        stopped = []
        for proc in psutil.process_iter(["pid", "cmdline"]):
            try:
                if "live_capture.py" in " ".join(proc.info.get("cmdline") or []):
                    proc.terminate()
                    stopped.append(proc.info["pid"])
            except:
                pass
        if stopped:
            return f"Live capture stopped (PIDs: {stopped})."
        return "Live capture was not running."

    return "Error: action must be 'start' or 'stop'."


if __name__ == "__main__":
    mcp.run()