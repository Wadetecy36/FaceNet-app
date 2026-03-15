#!/usr/bin/env node
// ============================================================
// facenet_mcp.js
// MCP stdio server for FaceNet-Node — LobeHub desktop
//
// Install deps (run once):
//   cd F:\FaceNet && npm install @modelcontextprotocol/sdk node-fetch
//
// LobeHub config:
//   {
//     "mcpServers": {
//       "facenet": {
//         "type": "stdio",
//         "command": "node",
//         "args": ["F:\\FaceNet\\facenet_mcp.js"]
//       }
//     }
//   }
// ============================================================

import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";
import fetch from "node-fetch";
import { execSync, spawn } from "child_process";

// ─── Config ───────────────────────────────────────────────
const FLASK_URL = "http://localhost:5000";
const YOLO_URL = "http://localhost:8000";
const FACENET_URL = "http://localhost:3001";
const PYTHON = String.raw`C:\Users\Trinity\AppData\Local\Programs\Python\Python313\python.exe`;
const CAPTURE_PY = String.raw`F:\FaceNet\live_capture.py`;

// ─── Server ───────────────────────────────────────────────
const server = new McpServer({
    name: "facenet-node-security",
    version: "1.0.0",
});

// ─── Tool 1: Query anomaly logs ───────────────────────────

server.tool(
    "query_anomalies",
    "Query FaceNet-Node anomaly detection logs. Filter by severity (high/medium/low/all), time window in hours, and whether to show only anomaly frames. Use this when asked about alerts, threats, detections, incidents, or person counts.",
    {
        severity: z.enum(["high", "medium", "low", "all"]).default("all").describe("Severity filter"),
        hours: z.number().int().min(1).optional().describe("Only return records from the last N hours"),
        limit: z.number().int().min(1).max(100).default(20).describe("Max records to return"),
        anomalies_only: z.boolean().default(false).describe("Only return frames that had anomalies"),
    },
    async ({ severity, hours, limit, anomalies_only }) => {
        const params = new URLSearchParams({
            severity,
            limit: String(limit),
            anomalies_only: String(anomalies_only),
        });
        if (hours) params.set("hours", String(hours));

        let data;
        try {
            const res = await fetch(`${FLASK_URL}/plugin/anomalies?${params}`, { signal: AbortSignal.timeout(5000) });
            data = await res.json();
        } catch (e) {
            return { content: [{ type: "text", text: `Cannot reach Flask logger: ${e.message}\nMake sure flask_logger.py is running on port 5000.` }] };
        }

        const summary = data.summary || {};
        const records = data.records || [];

        const lines = [
            "## Anomaly Log Summary",
            `- Total matched: ${summary.total_records ?? 0}`,
            `- High:   ${summary.high_severity ?? 0}`,
            `- Medium: ${summary.medium_severity ?? 0}`,
            `- Low:    ${summary.low_severity ?? 0}`,
            "",
        ];

        if (!records.length) {
            lines.push("No records found for the given filters.");
        } else {
            lines.push(`## Last ${records.length} Records`);
            for (const rec of records) {
                lines.push(`\n**[${(rec.severity || "?").toUpperCase()}]** ${rec.time || "?"} — ${rec.location || "?"}`);
                lines.push(`  Persons: ${rec.person_count ?? 0} detected, ${rec.unknown_count ?? 0} unknown`);
                for (const a of rec.anomalies || []) lines.push(`  ⚠ ${a}`);
                if (rec.log_entry) lines.push(`  Note: ${rec.log_entry}`);
            }
        }

        return { content: [{ type: "text", text: lines.join("\n") }] };
    }
);

// ─── Tool 2: System health ────────────────────────────────

server.tool(
    "get_system_health",
    "Get live health status of all FaceNet-Node services: Flask logger, YOLO server, FaceNet API, and live capture. Use when asked if the system is running or healthy.",
    {},
    async () => {
        const services = {};

        // Flask
        try {
            await fetch(`${FLASK_URL}/api/health`, { signal: AbortSignal.timeout(2000) });
            let records = 0;
            try {
                const s = await (await fetch(`${FLASK_URL}/api/stats`, { signal: AbortSignal.timeout(2000) })).json();
                records = s.total_records ?? 0;
            } catch { }
            services.flask_logger = { status: "online", port: 5000, records };
        } catch {
            services.flask_logger = { status: "OFFLINE", port: 5000 };
        }

        // YOLO
        try {
            const d = await (await fetch(`${YOLO_URL}/health`, { signal: AbortSignal.timeout(2000) })).json();
            services.yolo_server = { status: "online", port: 8000, model: d.yolo, llm: d.llm, ws_clients: d.clients ?? 0 };
        } catch {
            services.yolo_server = { status: "OFFLINE", port: 8000 };
        }

        // FaceNet API
        try {
            const users = await (await fetch(`${FACENET_URL}/api/users`, { signal: AbortSignal.timeout(2000) })).json();
            services.facenet_api = { status: "online", port: 3001, registered_users: Array.isArray(users) ? users.length : 0 };
        } catch {
            services.facenet_api = { status: "OFFLINE", port: 3001 };
        }

        // Live capture process
        let capturePid = null;
        try {
            const out = execSync('tasklist /FI "IMAGENAME eq python.exe" /FO CSV /NH', { encoding: "utf8" });
            const procs = execSync('wmic process where "name=\'python.exe\'" get ProcessId,CommandLine /FORMAT:CSV', { encoding: "utf8" });
            if (procs.includes("live_capture.py")) {
                const match = procs.match(/live_capture\.py[^\n]*,(\d+)/);
                if (match) capturePid = match[1];
                else capturePid = "unknown";
            }
        } catch { }
        services.live_capture = { status: capturePid ? `running (PID ${capturePid})` : "stopped" };

        const online = Object.values(services).filter(s => s.status.includes("online") || s.status.includes("running")).length;
        const total = Object.keys(services).length;

        const lines = [
            `## FaceNet-Node Health — ${online}/${total} online`,
            `Checked: ${new Date().toLocaleString()}`,
            "",
        ];

        for (const [name, info] of Object.entries(services)) {
            const st = info.status;
            const icon = st.includes("online") || st.includes("running") ? "✅" : st === "stopped" ? "⚠️" : "❌";
            lines.push(`${icon} **${name}** — ${st}`);
            for (const [k, v] of Object.entries(info)) {
                if (k !== "status") lines.push(`   ${k}: ${v}`);
            }
        }

        if (online < total) lines.push("\n⚠️ Some services offline — run start-facenet.ps1 to restart.");

        return { content: [{ type: "text", text: lines.join("\n") }] };
    }
);

// ─── Tool 3: Control live capture ────────────────────────

server.tool(
    "control_capture",
    "Start or stop the FaceNet-Node live capture pipeline. Use when asked to start/stop monitoring, the camera, or surveillance.",
    {
        action: z.enum(["start", "stop"]).describe("start or stop live capture"),
    },
    async ({ action }) => {
        if (action === "start") {
            // Check if already running
            try {
                const procs = execSync('wmic process where "name=\'python.exe\'" get CommandLine /FORMAT:CSV', { encoding: "utf8" });
                if (procs.includes("live_capture.py")) {
                    return { content: [{ type: "text", text: "✅ Live capture is already running." }] };
                }
            } catch { }

            const child = spawn(PYTHON, [CAPTURE_PY], {
                detached: true,
                stdio: "ignore",
                windowsHide: false,
            });
            child.unref();
            return { content: [{ type: "text", text: `✅ Live capture started (PID ${child.pid}). Camera feed is now being processed.` }] };
        }

        if (action === "stop") {
            try {
                execSync(`taskkill /F /FI "WINDOWTITLE eq live_capture*" /IM python.exe`, { encoding: "utf8" });
            } catch { }
            try {
                const result = execSync(
                    `wmic process where "CommandLine like '%live_capture.py%' and name='python.exe'" delete`,
                    { encoding: "utf8" }
                );
                if (result.includes("successful")) {
                    return { content: [{ type: "text", text: "🛑 Live capture stopped." }] };
                }
            } catch { }
            return { content: [{ type: "text", text: "ℹ️ Live capture was not running." }] };
        }

        return { content: [{ type: "text", text: "Error: action must be 'start' or 'stop'." }] };
    }
);

// ─── Start ────────────────────────────────────────────────

const transport = new StdioServerTransport();
await server.connect(transport);

// Keep the process alive for LobeHub's handshake
process.stdin.resume();
process.on("SIGINT", () => process.exit(0));
process.on("SIGTERM", () => process.exit(0));