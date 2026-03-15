/**
 * server-inference.ts
 * ====================
 * Drop these additions into your existing server.ts.
 * Shows how to wire InferenceClient + AnomalyLogger into a running Node server
 * with WebSocket broadcast so the dashboard gets live anomaly events.
 *
 * Assumes you already have:
 *   - Express app (or equivalent)
 *   - WebSocket server (ws library)
 *   - Session management
 */

import { InferenceClient, type KnownFace, type InferenceResult } from "./inference-client.js";
import { AnomalyLogger, type LogRecord } from "./anomaly-logger.js";
import type { WebSocket, WebSocketServer } from "ws";

// ─── Instantiate (add to your server startup) ─────────────────────────────────

export function createInferencePipeline(
  wss: WebSocketServer,
  opts: {
    inferenceServerUrl: string;   // e.g. "http://192.168.1.50:8000"
    deviceId: string;
    location?: string;
    flaskEndpoint?: string;
  }
) {
  // Inference client — talks to LAN YOLO+Qwen server
  const client = new InferenceClient({
    serverUrl:            opts.inferenceServerUrl,
    deviceId:             opts.deviceId,
    location:             opts.location ?? "",
    inferenceIntervalMs:  500,    // 2fps to inference server; adjust to taste
    timeoutMs:            10_000,
  });

  // Logger — ring buffer + NDJSON file + optional Flask relay
  const logger = new AnomalyLogger({
    persistPath:   "./logs/events.ndjson",
    flaskEndpoint: opts.flaskEndpoint ?? null,
  });

  // ── Wire up events ────────────────────────────────────────────────────────

  client.onResult(async (result: InferenceResult) => {
    const record = await logger.record(result);

    // Broadcast ALL results to dashboard clients (for live person count, etc.)
    broadcast(wss, {
      type:    "inference_result",
      payload: record,
    });
  });

  client.onAnomaly((anomaly, result) => {
    // Broadcast anomaly separately for urgent UI highlighting
    broadcast(wss, {
      type:    "anomaly",
      payload: {
        severity:   anomaly.severity,
        type:       anomaly.type,
        description: anomaly.description,
        recommended_action: anomaly.recommended_action,
        device_id:  result.device_id,
        session_id: result.session_id,
        timestamp:  result.timestamp,
      },
    });
  });

  client.onError((err) => {
    console.error("[Inference] Error:", err.message);
    broadcast(wss, { type: "inference_error", payload: { message: err.message } });
  });

  return { client, logger };
}

// ─── REST endpoints to add to your Express app ────────────────────────────────

/**
 * GET /api/logs?session=xxx&anomalies_only=true&severity=high&limit=50
 * Returns recent log records from the ring buffer.
 */
export function registerLogRoutes(
  app: any,                          // Express app
  logger: AnomalyLogger,
) {
  // Recent logs (all or filtered)
  app.get("/api/logs", (req: any, res: any) => {
    const { session, device, anomalies_only, severity, limit, since } = req.query;

    const records = logger.query({
      sessionId:     session as string | undefined,
      deviceId:      device  as string | undefined,
      onlyAnomalies: anomalies_only === "true",
      minSeverity:   severity as "low" | "medium" | "high" | undefined,
      limit:         limit ? parseInt(limit as string, 10) : 100,
      since:         since ? parseInt(since as string, 10) : undefined,
    });

    res.json({ count: records.length, records });
  });

  // Plain-text anomaly summary for a session
  app.get("/api/logs/summary/:sessionId", (req: any, res: any) => {
    const summary = logger.summaryText(req.params.sessionId);
    res.type("text/plain").send(summary);
  });
}

// ─── WebSocket broadcast helper ───────────────────────────────────────────────

function broadcast(wss: WebSocketServer, message: object): void {
  const json = JSON.stringify(message);
  wss.clients.forEach((ws: WebSocket) => {
    if (ws.readyState === 1 /* OPEN */) {
      ws.send(json);
    }
  });
}

// ─── Usage example (paste into your server.ts) ────────────────────────────────
/*

import { createInferencePipeline, registerLogRoutes } from "./server-inference.js";

// After your wss and app are created:
const { client, logger } = createInferencePipeline(wss, {
  inferenceServerUrl: process.env.INFERENCE_SERVER ?? "http://192.168.1.50:8000",
  deviceId:           process.env.DEVICE_ID ?? "edge-device-1",
  location:           process.env.LOCATION  ?? "Main Entrance",
  flaskEndpoint:      process.env.FLASK_URL  ?? undefined,
});

registerLogRoutes(app, logger);

// When a session starts (e.g. on WebSocket message from dashboard):
client.start(
  sessionId,
  async () => {
    // Return the latest frame as base64 JPEG from your WebRTC/OpenCV pipeline
    return await captureFrameAsBase64();
  },
  () => {
    // Return the latest FaceNet results
    return getCurrentKnownFaces();
  }
);

// On server shutdown:
process.on("SIGTERM", () => {
  client.stop();
  logger.close();
});

*/
