/**
 * src/inference-client.ts
 * =======================
 * Connects FaceNet-Node's Node.js backend to the YOLO + Qwen LAN inference server.
 * Called per-frame (or on a configurable interval) during active sessions.
 *
 * Flow:
 *   capture frame (WebRTC/OpenCV)
 *     → encode to base64 JPEG
 *       → POST /infer to LAN server
 *         → receive detections + anomalies + log entry
 *           → store via AnomalyLogger
 *             → emit to connected clients via WebSocket
 */

import type { WebSocket } from "ws";

// ─── Types ────────────────────────────────────────────────────────────────────

export interface KnownFace {
  id: string;
  name: string;
  confidence: number; // 0–1, from FaceNet
}

export interface Detection {
  label: string;
  confidence: number;
  bbox: [number, number, number, number]; // [x1, y1, x2, y2]
}

export interface AnomalyFlag {
  severity: "low" | "medium" | "high";
  type: string;
  description: string;
  recommended_action: string;
}

export interface InferenceResult {
  device_id: string;
  session_id: string;
  timestamp: number;
  detections: Detection[];
  person_count: number;
  known_face_ids: string[];
  anomalies: AnomalyFlag[];
  log_entry: string;
  processing_ms: number;
}

export interface InferenceClientConfig {
  /** e.g. "http://192.168.1.50:8000" — the LAN machine running yolo_server.py */
  serverUrl: string;
  /** Unique ID for this edge device */
  deviceId: string;
  /** Timeout per request in ms (default: 10000) */
  timeoutMs?: number;
  /** How often to send frames for inference, in ms (default: 500 = 2fps) */
  inferenceIntervalMs?: number;
  /** Room or zone label for logs */
  location?: string;
}

// ─── InferenceClient ──────────────────────────────────────────────────────────

export class InferenceClient {
  private config: Required<InferenceClientConfig>;
  private isRunning = false;
  private intervalHandle?: ReturnType<typeof setInterval>;

  /** Callbacks registered by the application layer */
  private onResultCallbacks: Array<(result: InferenceResult) => void> = [];
  private onAnomalyCallbacks: Array<(anomaly: AnomalyFlag, result: InferenceResult) => void> = [];
  private onErrorCallbacks: Array<(err: Error) => void> = [];

  constructor(config: InferenceClientConfig) {
    this.config = {
      timeoutMs: 10_000,
      inferenceIntervalMs: 500,
      location: "",
      ...config,
    };
  }

  // ─── Lifecycle ─────────────────────────────────────────────────────────────

  /**
   * Verify the LAN inference server is reachable before starting.
   */
  async ping(): Promise<boolean> {
    try {
      const resp = await fetch(`${this.config.serverUrl}/health`, {
        signal: AbortSignal.timeout(3000),
      });
      return resp.ok;
    } catch {
      return false;
    }
  }

  /**
   * Start sending frames at the configured interval.
   * `getFrameFn` is called each tick — supply your WebRTC/OpenCV frame source here.
   * `getKnownFacesFn` returns the latest FaceNet results for this frame.
   * `sessionId` identifies the current attendance session.
   */
  start(
    sessionId: string,
    getFrameFn: () => Promise<string | null>,         // returns base64 JPEG or null to skip
    getKnownFacesFn: () => KnownFace[],
  ): void {
    if (this.isRunning) return;
    this.isRunning = true;

    this.intervalHandle = setInterval(async () => {
      if (!this.isRunning) return;

      const frame = await getFrameFn();
      if (!frame) return; // camera not ready yet

      await this.sendFrame(frame, sessionId, getKnownFacesFn());
    }, this.config.inferenceIntervalMs);
  }

  /** Stop the inference loop. */
  stop(): void {
    this.isRunning = false;
    if (this.intervalHandle) {
      clearInterval(this.intervalHandle);
      this.intervalHandle = undefined;
    }
  }

  // ─── Core send ─────────────────────────────────────────────────────────────

  /**
   * Send a single frame to the inference server immediately.
   * Can also be called manually (e.g. on motion trigger).
   */
  async sendFrame(
    frameB64: string,
    sessionId: string,
    knownFaces: KnownFace[] = [],
  ): Promise<InferenceResult | null> {
    const payload = {
      frame_b64: frameB64,
      device_id: this.config.deviceId,
      session_id: sessionId,
      timestamp: Date.now() / 1000,
      known_faces: knownFaces,
      location: this.config.location,
    };

    try {
      const resp = await fetch(`${this.config.serverUrl}/infer`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        signal: AbortSignal.timeout(this.config.timeoutMs),
      });

      if (!resp.ok) {
        throw new Error(`Server returned ${resp.status}: ${await resp.text()}`);
      }

      const result: InferenceResult = await resp.json();

      // Notify result listeners
      this.onResultCallbacks.forEach((cb) => cb(result));

      // Notify anomaly listeners for each flagged anomaly
      result.anomalies.forEach((anomaly) => {
        this.onAnomalyCallbacks.forEach((cb) => cb(anomaly, result));
      });

      return result;
    } catch (err) {
      const error = err instanceof Error ? err : new Error(String(err));
      this.onErrorCallbacks.forEach((cb) => cb(error));
      return null;
    }
  }

  // ─── Event registration ────────────────────────────────────────────────────

  /** Fires for every successful inference result. */
  onResult(cb: (result: InferenceResult) => void): this {
    this.onResultCallbacks.push(cb);
    return this;
  }

  /** Fires for every anomaly in a result — more convenient than filtering onResult. */
  onAnomaly(cb: (anomaly: AnomalyFlag, result: InferenceResult) => void): this {
    this.onAnomalyCallbacks.push(cb);
    return this;
  }

  /** Fires on network/server errors. */
  onError(cb: (err: Error) => void): this {
    this.onErrorCallbacks.push(cb);
    return this;
  }
}
