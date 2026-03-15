"""
flask_logger.py
===============
Central database logger for FaceNet-Node inference results.
Receives anomaly records from the Node.js backend and stores them in SQLite.

Run:
    pip install flask flask-sqlalchemy flask-cors
    python flask_logger.py

Endpoints:
    POST /api/anomalies     ← receives records from Node.js
    GET  /api/anomalies     ← query stored records
    GET  /api/stats         ← session statistics
    GET  /api/health        ← health check
"""

from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
from datetime import datetime
import json
import os

# ─── Config ───────────────────────────────────────────────────────────────────

DB_PATH  = "F:/FaceNet/facenet_logs.db"
PORT     = 5000
HOST     = "0.0.0.0"

# ─── App setup ────────────────────────────────────────────────────────────────

app = Flask(__name__)
CORS(app)

app.config["SQLALCHEMY_DATABASE_URI"]        = f"sqlite:///{DB_PATH}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)

# ─── Models ───────────────────────────────────────────────────────────────────

class InferenceLog(db.Model):
    __tablename__ = "inference_logs"

    id            = db.Column(db.Integer,  primary_key=True)
    record_id     = db.Column(db.String,   unique=True, nullable=False)
    device_id     = db.Column(db.String,   nullable=False, index=True)
    session_id    = db.Column(db.String,   nullable=False, index=True)
    timestamp_ms  = db.Column(db.BigInteger, nullable=False)
    iso_time      = db.Column(db.String,   nullable=False)
    location      = db.Column(db.String,   default="")
    person_count  = db.Column(db.Integer,  default=0)
    known_count   = db.Column(db.Integer,  default=0)
    unknown_count = db.Column(db.Integer,  default=0)
    has_anomaly   = db.Column(db.Boolean,  default=False, index=True)
    max_severity  = db.Column(db.String,   default="none", index=True)
    log_entry     = db.Column(db.Text,     default="")
    anomalies_json= db.Column(db.Text,     default="[]")   # JSON string
    detections_json= db.Column(db.Text,   default="[]")   # JSON string
    processing_ms = db.Column(db.Float,   default=0)
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id":            self.id,
            "record_id":     self.record_id,
            "device_id":     self.device_id,
            "session_id":    self.session_id,
            "timestamp_ms":  self.timestamp_ms,
            "iso_time":      self.iso_time,
            "location":      self.location,
            "person_count":  self.person_count,
            "known_count":   self.known_count,
            "unknown_count": self.unknown_count,
            "has_anomaly":   self.has_anomaly,
            "max_severity":  self.max_severity,
            "log_entry":     self.log_entry,
            "anomalies":     json.loads(self.anomalies_json),
            "detections":    json.loads(self.detections_json),
            "processing_ms": self.processing_ms,
            "created_at":    self.created_at.isoformat(),
        }

# ─── Routes ───────────────────────────────────────────────────────────────────

@app.route("/api/health")
def health():
    count = InferenceLog.query.count()
    return jsonify({"status": "ok", "total_records": count, "db": DB_PATH})


@app.route("/api/anomalies", methods=["POST"])
def receive_log():
    """
    Receives inference records from Node.js backend or yolo_server.py directly.
    Accepts single record or array of records.
    """
    data = request.get_json(force=True)
    if not data:
        return jsonify({"error": "No JSON body"}), 400

    # Accept single record or array
    records = data if isinstance(data, list) else [data]
    saved   = 0
    skipped = 0

    for rec in records:
        record_id = rec.get("id") or f"{rec.get('device_id','?')}:{rec.get('timestamp_ms', rec.get('timestamp',0))}"

        # Skip duplicates
        if InferenceLog.query.filter_by(record_id=record_id).first():
            skipped += 1
            continue

        ts = rec.get("timestamp_ms") or int(rec.get("timestamp", 0) * 1000)

        log = InferenceLog(
            record_id      = record_id,
            device_id      = rec.get("device_id", "unknown"),
            session_id     = rec.get("session_id", "unknown"),
            timestamp_ms   = ts,
            iso_time       = rec.get("iso_time", datetime.utcnow().isoformat()),
            location       = rec.get("location", ""),
            person_count   = rec.get("person_count", 0),
            known_count    = rec.get("known_count", 0),
            unknown_count  = rec.get("unknown_count", 0),
            has_anomaly    = rec.get("has_anomaly", len(rec.get("anomalies", [])) > 0),
            max_severity   = rec.get("max_severity", "none"),
            log_entry      = rec.get("log_entry", ""),
            anomalies_json = json.dumps(rec.get("anomalies", [])),
            detections_json= json.dumps(rec.get("detections", [])),
            processing_ms  = rec.get("processing_ms", 0),
        )
        db.session.add(log)
        saved += 1

    db.session.commit()
    return jsonify({"saved": saved, "skipped": skipped}), 201


@app.route("/api/anomalies", methods=["GET"])
def get_logs():
    """
    Query stored records.
    Params: session, device, anomalies_only, severity, limit, offset
    """
    session       = request.args.get("session")
    device        = request.args.get("device")
    anomalies_only= request.args.get("anomalies_only") == "true"
    severity      = request.args.get("severity")
    limit         = int(request.args.get("limit", 100))
    offset        = int(request.args.get("offset", 0))

    q = InferenceLog.query

    if session:        q = q.filter_by(session_id=session)
    if device:         q = q.filter_by(device_id=device)
    if anomalies_only: q = q.filter_by(has_anomaly=True)
    if severity:       q = q.filter_by(max_severity=severity)

    total   = q.count()
    records = q.order_by(InferenceLog.timestamp_ms.desc()).offset(offset).limit(limit).all()

    return jsonify({
        "total":   total,
        "count":   len(records),
        "records": [r.to_dict() for r in records],
    })


@app.route("/api/stats")
def get_stats():
    """Aggregate stats per session."""
    session = request.args.get("session")
    q = InferenceLog.query
    if session: q = q.filter_by(session_id=session)

    total       = q.count()
    anomalies   = q.filter_by(has_anomaly=True).count()
    high        = q.filter_by(max_severity="high").count()
    medium      = q.filter_by(max_severity="medium").count()
    low         = q.filter_by(max_severity="low").count()
    avg_persons = db.session.query(
        db.func.avg(InferenceLog.person_count)
    ).scalar() or 0

    return jsonify({
        "total_frames":    total,
        "anomaly_frames":  anomalies,
        "high_severity":   high,
        "medium_severity": medium,
        "low_severity":    low,
        "avg_persons":     round(float(avg_persons), 2),
    })


@app.route("/api/sessions")
def get_sessions():
    """List all unique session IDs."""
    sessions = db.session.query(
        InferenceLog.session_id,
        db.func.count(InferenceLog.id).label("frames"),
        db.func.min(InferenceLog.iso_time).label("started"),
        db.func.max(InferenceLog.iso_time).label("last_seen"),
    ).group_by(InferenceLog.session_id).all()

    return jsonify([{
        "session_id": s.session_id,
        "frames":     s.frames,
        "started":    s.started,
        "last_seen":  s.last_seen,
    } for s in sessions])


# ─── LobeChat Plugin Routes ───────────────────────────────────────────────────

try:
    from lobe_plugin import register_plugin_routes
    register_plugin_routes(app, db, InferenceLog)
    print("[OK] LobeChat plugin routes registered")
    print("     Manifest: http://localhost:5000/.well-known/ai-plugin.json")
except ImportError:
    print("[!] lobe_plugin.py not found — plugin routes disabled")

# ─── Init ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        print(f"Database: {DB_PATH}")
        print(f"Records:  {InferenceLog.query.count()}")
    print(f"Flask logger running on http://{HOST}:{PORT}")
    app.run(host=HOST, port=PORT, debug=False)
