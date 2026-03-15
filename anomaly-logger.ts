/**
 * src/anomaly-logger.ts
 * =====================
 * Handles structured storage of inference results, anomaly flags, and log entries.
 *
 * Storage strategy:
 *  - In-memory ring buffer for fast recent-event queries (dashboard live feed).
 *  - Append-only NDJSON file for durable log persistence.
 *  - Optional: POST to Flask-SQLAlchemy endpoint (your existing backend).
 *
 * Usage:
 *   const logger = new AnomalyLogger({ persistPath: "./logs/events.ndjson" });
 *   logger.record(inferenceResult);
 */

import fs from "fs";
import path from "path";
import type { InferenceResult, AnomalyFlag } from "./inference-client.js";

// ─── Types ────────────────────────────────────────────────────────────────────

export interface LogRecord {
  id: string;                   // "<device_id>:<timestamp_ms>"
  device_id: string;
  session_id: string;
  timestamp_ms: number;
  iso_time: string;
  location: string;
  person_count: number;
  known_face_ids: string[];
  anomalies: AnomalyFlag[];
  log_entry: string;            // Qwen-generated human-readable line
  has_anomaly: boolean;
  max_severity: "none" | "low" | "medium" | "high";
  processing_ms: number;
}

export interface AnomalyLoggerConfig {
  /** Path to write NDJSON log file. Set to null to disable file persistence. */
  persistPath?: string | null;
  /** Max records to keep in memory (ring buffer). Default: 500 */
  ringBufferSize?: number;
  /** Optional: Flask-SQLAlchemy endpoint to also POST records to */
  flaskEndpoint?: string | null;
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

const SEVERITY_RANK: Record<string, number> = { none: 0, low: 1, medium: 2, high: 3 };

function maxSeverity(anomalies: AnomalyFlag[]): LogRecord["max_severity"] {
  if (!anomalies.length) return "none";
  return anomalies.reduce<LogRecord["max_severity"]>((max, a) => {
    return SEVERITY_RANK[a.severity] > SEVERITY_RANK[max] ? a.severity : max;
  }, "none");
}

function buildRecord(result: InferenceResult): LogRecord {
  const ts_ms = Math.round(result.timestamp * 1000);
  return {
    id:             `${result.device_id}:${ts_ms}`,
    device_id:      result.device_id,
    session_id:     result.session_id,
    timestamp_ms:   ts_ms,
    iso_time:       new Date(ts_ms).toISOString(),
    location:       "",               // populated by server.ts if needed
    person_count:   result.person_count,
    known_face_ids: result.known_face_ids,
    anomalies:      result.anomalies,
    log_entry:      result.log_entry,
    has_anomaly:    result.anomalies.length > 0,
    max_severity:   maxSeverity(result.anomalies),
    processing_ms:  result.processing_ms,
  };
}

// ─── AnomalyLogger ────────────────────────────────────────────────────────────

export class AnomalyLogger {
  private config: Required<AnomalyLoggerConfig>;
  private buffer: LogRecord[] = [];          // Ring buffer (most recent first)
  private writeStream: fs.WriteStream | null = null;

  constructor(config: AnomalyLoggerConfig = {}) {
    this.config = {
      persistPath:   "./logs/events.ndjson",
      ringBufferSize: 500,
      flaskEndpoint: null,
      ...config,
    };

    if (this.config.persistPath) {
      const dir = path.dirname(this.config.persistPath);
      fs.mkdirSync(dir, { recursive: true });
      this.writeStream = fs.createWriteStream(this.config.persistPath, { flags: "a" });
    }
  }

  // ─── Public API ─────────────────────────────────────────────────────────────

  /** Record an inference result. Call this in your onResult() handler. */
  async record(result: InferenceResult): Promise<LogRecord> {
    const record = buildRecord(result);

    // 1. Add to ring buffer
    this.buffer.unshift(record);
    if (this.buffer.length > this.config.ringBufferSize) {
      this.buffer.pop();
    }

    // 2. Append to NDJSON file
    if (this.writeStream) {
      this.writeStream.write(JSON.stringify(record) + "\n");
    }

    // 3. Forward to Flask (fire-and-forget)
    if (this.config.flaskEndpoint && record.has_anomaly) {
      this.postToFlask(record).catch((e) =>
        console.error("[AnomalyLogger] Flask POST failed:", e.message)
      );
    }

    // 4. Console log for high severity
    if (record.max_severity === "high") {
      console.error(
        `\x1b[31m[ALERT]\x1b[0m ${record.iso_time} | ${record.device_id} | ` +
        record.anomalies
          .filter((a) => a.severity === "high")
          .map((a) => `${a.type}: ${a.description}`)
          .join(" | ")
      );
    }

    return record;
  }

  /**
   * Query the in-memory buffer.
   * All params are optional — omit to get everything.
   */
  query(opts: {
    sessionId?: string;
    deviceId?: string;
    onlyAnomalies?: boolean;
    minSeverity?: "low" | "medium" | "high";
    limit?: number;
    since?: number;            // Unix timestamp ms
  } = {}): LogRecord[] {
    let results = this.buffer;

    if (opts.sessionId)    results = results.filter((r) => r.session_id === opts.sessionId);
    if (opts.deviceId)     results = results.filter((r) => r.device_id  === opts.deviceId);
    if (opts.onlyAnomalies) results = results.filter((r) => r.has_anomaly);
    if (opts.since)        results = results.filter((r) => r.timestamp_ms >= opts.since!);
    if (opts.minSeverity) {
      const minRank = SEVERITY_RANK[opts.minSeverity];
      results = results.filter((r) => SEVERITY_RANK[r.max_severity] >= minRank);
    }
    if (opts.limit)        results = results.slice(0, opts.limit);

    return results;
  }

  /** Get a plain-text summary of recent anomalies for a session (useful for dashboards). */
  summaryText(sessionId: string): string {
    const anomalyRecords = this.query({ sessionId, onlyAnomalies: true, limit: 20 });
    if (!anomalyRecords.length) return "No anomalies detected in this session.";
    return anomalyRecords
      .map((r) =>
        r.anomalies
          .map((a) => `[${r.iso_time}] [${a.severity.toUpperCase()}] ${a.type}: ${a.description}`)
          .join("\n")
      )
      .join("\n");
  }

  /** Flush and close the log file. Call on server shutdown. */
  close(): void {
    this.writeStream?.end();
  }

  // ─── Private ────────────────────────────────────────────────────────────────

  private async postToFlask(record: LogRecord): Promise<void> {
    const resp = await fetch(this.config.flaskEndpoint!, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(record),
      signal: AbortSignal.timeout(5000),
    });
    if (!resp.ok) throw new Error(`Flask returned ${resp.status}`);
  }
}
