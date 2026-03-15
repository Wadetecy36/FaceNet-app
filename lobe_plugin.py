# ============================================================
# lobe_plugin.py
# LobeChat Plugin for FaceNet-Node
#
# Adds these routes to Flask (port 5000):
#   GET  /.well-known/ai-plugin.json   → plugin manifest
#   GET  /openapi.json                 → OpenAPI spec
#   GET  /plugin/anomalies             → query logs
#   GET  /plugin/health                → system health
#   POST /plugin/capture               → start/stop live capture
#
# Usage:
#   Import this file into flask_logger.py (see bottom of file)
#   OR run standalone: python lobe_plugin.py
# ============================================================

import os
import json
import subprocess
import psutil
import requests
from datetime import datetime, timedelta
from flask import Flask, request, jsonify
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy

# ─── Shared app setup (reuse flask_logger.py app if imported) ─────────────────

def register_plugin_routes(app, db, InferenceLog):
    """
    Call this from flask_logger.py to add plugin routes.
    Pass in the existing app, db, and InferenceLog model.
    """

    # ── Manifest ──────────────────────────────────────────────────────────────

    @app.route("/.well-known/ai-plugin.json")
    def plugin_manifest():
        return jsonify({
            "schema_version": "v1",
            "name_for_human": "FaceNet-Node Security",
            "name_for_model": "facenet_security",
            "description_for_human": "Query FaceNet-Node anomaly logs, system health, and control live capture.",
            "description_for_model": (
                "Use this plugin to query FaceNet-Node security logs. "
                "You can filter anomalies by severity (high/medium/low) and time range, "
                "get system health status, and start or stop the live capture pipeline. "
                "Always summarize results in a clear, concise way."
            ),
            "auth": {"type": "none"},
            "api": {
                "type": "openapi",
                "url": "http://localhost:5000/openapi.json",
            },
            "logo_url": "http://localhost:5000/logo.png",
            "contact_email": "admin@facenet.local",
            "legal_info_url": "http://localhost:5000",
        })

    # ── OpenAPI spec ──────────────────────────────────────────────────────────

    @app.route("/openapi.json")
    def openapi_spec():
        return jsonify({
            "openapi": "3.0.0",
            "info": {
                "title": "FaceNet-Node Security API",
                "version": "1.0.0",
                "description": "Query anomaly logs, health, and control live capture.",
            },
            "servers": [{"url": "http://localhost:5000"}],
            "paths": {
                "/plugin/anomalies": {
                    "get": {
                        "operationId": "getAnomalies",
                        "summary": "Query anomaly logs",
                        "description": (
                            "Returns anomaly detection records. "
                            "Filter by severity, time range, or limit count. "
                            "Use this when the user asks about alerts, detections, threats, or incidents."
                        ),
                        "parameters": [
                            {
                                "name": "severity",
                                "in": "query",
                                "description": "Filter by severity: high, medium, low, or all",
                                "required": False,
                                "schema": {"type": "string", "enum": ["high","medium","low","all"]},
                            },
                            {
                                "name": "hours",
                                "in": "query",
                                "description": "Only return records from the last N hours (e.g. 1, 6, 24)",
                                "required": False,
                                "schema": {"type": "integer"},
                            },
                            {
                                "name": "limit",
                                "in": "query",
                                "description": "Max number of records to return (default 20)",
                                "required": False,
                                "schema": {"type": "integer"},
                            },
                            {
                                "name": "anomalies_only",
                                "in": "query",
                                "description": "If true, only return frames that had anomalies",
                                "required": False,
                                "schema": {"type": "boolean"},
                            },
                        ],
                        "responses": {
                            "200": {
                                "description": "List of anomaly records with summary stats",
                            }
                        },
                    }
                },
                "/plugin/health": {
                    "get": {
                        "operationId": "getSystemHealth",
                        "summary": "Get FaceNet-Node system health",
                        "description": (
                            "Returns the health status of all FaceNet-Node services: "
                            "Flask logger, YOLO inference server, FaceNet-Node API, and live capture. "
                            "Use this when the user asks if the system is running, online, or healthy."
                        ),
                        "responses": {
                            "200": {"description": "Health status of all services"}
                        },
                    }
                },
                "/plugin/capture": {
                    "post": {
                        "operationId": "controlCapture",
                        "summary": "Start or stop live capture",
                        "description": (
                            "Start or stop the live_capture.py pipeline. "
                            "Use action=start to begin capturing, action=stop to end it. "
                            "Use this when the user says 'start monitoring', 'stop the camera', etc."
                        ),
                        "requestBody": {
                            "required": True,
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "action": {
                                                "type": "string",
                                                "enum": ["start", "stop"],
                                                "description": "start or stop the capture process",
                                            }
                                        },
                                        "required": ["action"],
                                    }
                                }
                            },
                        },
                        "responses": {
                            "200": {"description": "Capture control result"}
                        },
                    }
                },
            },
        })

    # ── /plugin/anomalies ─────────────────────────────────────────────────────

    @app.route("/plugin/anomalies")
    def plugin_anomalies():
        severity      = request.args.get("severity", "all")
        hours         = request.args.get("hours",    type=int)
        limit         = request.args.get("limit",    default=20, type=int)
        anomalies_only= request.args.get("anomalies_only", "false").lower() == "true"

        q = InferenceLog.query

        if severity and severity != "all":
            q = q.filter_by(max_severity=severity)

        if anomalies_only:
            q = q.filter_by(has_anomaly=True)

        if hours:
            since = datetime.utcnow() - timedelta(hours=hours)
            q = q.filter(InferenceLog.created_at >= since)

        total   = q.count()
        records = q.order_by(InferenceLog.timestamp_ms.desc()).limit(limit).all()

        # Build summary stats
        high   = q.filter_by(max_severity="high").count()
        medium = q.filter_by(max_severity="medium").count()
        low    = q.filter_by(max_severity="low").count()

        # Format records for LLM consumption — concise, no raw blobs
        formatted = []
        for r in records:
            anomalies = json.loads(r.anomalies_json) if r.anomalies_json else []
            formatted.append({
                "time":          r.iso_time,
                "location":      r.location,
                "person_count":  r.person_count,
                "unknown_count": r.unknown_count,
                "severity":      r.max_severity,
                "anomalies":     [
                    f"[{a.get('severity','?').upper()}] {a.get('type','?')}: {a.get('description','')}"
                    for a in anomalies
                ],
                "log_entry":     r.log_entry,
            })

        return jsonify({
            "summary": {
                "total_records":   total,
                "high_severity":   high,
                "medium_severity": medium,
                "low_severity":    low,
                "filter_applied":  {
                    "severity":       severity,
                    "last_n_hours":   hours,
                    "anomalies_only": anomalies_only,
                },
            },
            "records": formatted,
        })

    # ── /plugin/health ────────────────────────────────────────────────────────

    @app.route("/plugin/health")
    def plugin_health():
        services = {}

        # Flask itself
        services["flask_logger"] = {
            "status": "online",
            "port": 5000,
            "records": InferenceLog.query.count(),
        }

        # YOLO server
        try:
            r = requests.get("http://localhost:8000/health", timeout=2)
            data = r.json()
            services["yolo_server"] = {
                "status":  "online",
                "port":    8000,
                "yolo":    data.get("yolo"),
                "llm":     data.get("llm"),
                "clients": data.get("clients", 0),
            }
        except:
            services["yolo_server"] = {"status": "offline", "port": 8000}

        # FaceNet-Node API
        try:
            r = requests.get("http://localhost:3001/api/users", timeout=2)
            users = r.json()
            services["facenet_api"] = {
                "status":           "online",
                "port":             3001,
                "registered_users": len(users) if isinstance(users, list) else 0,
            }
        except:
            services["facenet_api"] = {"status": "offline", "port": 3001}

        # Live capture process
        capture_running = False
        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                cmdline = " ".join(proc.info.get("cmdline") or [])
                if "live_capture.py" in cmdline:
                    capture_running = True
                    break
            except:
                pass

        services["live_capture"] = {
            "status": "running" if capture_running else "stopped",
        }

        # Overall status
        online_count = sum(1 for s in services.values() if s.get("status") in ("online","running"))
        overall = "all systems operational" if online_count == 4 else \
                  f"{online_count}/4 services online"

        return jsonify({
            "overall": overall,
            "services": services,
            "timestamp": datetime.utcnow().isoformat(),
        })

    # ── /plugin/capture ───────────────────────────────────────────────────────

    _capture_process = {}   # store subprocess reference

    @app.route("/plugin/capture", methods=["POST"])
    def plugin_capture():
        data   = request.get_json(force=True)
        action = data.get("action", "").lower()

        PYTHON       = r"C:\Users\Trinity\AppData\Local\Programs\Python\Python313\python.exe"
        CAPTURE_FILE = r"F:\FaceNet\live_capture.py"

        if action == "start":
            # Check if already running
            for proc in psutil.process_iter(["pid", "cmdline"]):
                try:
                    if "live_capture.py" in " ".join(proc.info.get("cmdline") or []):
                        return jsonify({
                            "status":  "already_running",
                            "message": "Live capture is already running.",
                        })
                except:
                    pass

            # Start it
            proc = subprocess.Popen(
                [PYTHON, CAPTURE_FILE],
                creationflags=subprocess.CREATE_NEW_CONSOLE,
            )
            _capture_process["pid"] = proc.pid
            return jsonify({
                "status":  "started",
                "pid":     proc.pid,
                "message": "Live capture pipeline started successfully.",
            })

        elif action == "stop":
            stopped = []
            for proc in psutil.process_iter(["pid", "name", "cmdline"]):
                try:
                    if "live_capture.py" in " ".join(proc.info.get("cmdline") or []):
                        proc.terminate()
                        stopped.append(proc.info["pid"])
                except:
                    pass

            if stopped:
                return jsonify({
                    "status":  "stopped",
                    "pids":    stopped,
                    "message": f"Live capture stopped (PID {stopped}).",
                })
            return jsonify({
                "status":  "not_running",
                "message": "Live capture was not running.",
            })

        return jsonify({"error": "action must be 'start' or 'stop'"}), 400
