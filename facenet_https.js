// ============================================================
// facenet_mcp_http.js (v2)
// Streamable HTTP MCP server for LobeHub
//
// Run:   node F:\FaceNet\facenet_mcp_http.js
// URL:   http://localhost:3333/mcp
// ============================================================

import express from "express";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import { z } from "zod";
import fetch from "node-fetch";
import { execSync, spawn } from "child_process";

const PORT = 3333;
const FLASK_URL = "http://localhost:5000";
const YOLO_URL = "http://localhost:8000";
const FACENET_URL = "http://localhost:3001";
const PYTHON = String.raw`C:\Users\Trinity\AppData\Local\Programs\Python\Python313\python.exe`;
const CAPTURE_PY = String.raw`F:\FaceNet\live_capture.py`;

// ─── Register tools on any McpServer instance ─────────────
function registerTools(s) {

    s.tool(
        "query_anomalies",
        "Query FaceNet-Node anomaly detection logs. Filter by severity (high/medium/low/all), time window in hours, and anomalies_only flag. Use when asked about alerts, threats, detections, incidents, or person counts.",
        {
            severity: z.enum(["high", "medium", "low", "all"]).default("all"),
            hours: z.number().int().min(1).optional(),
            limit: z.number().int().min(1).max(100).default(20),
            anomalies_only: z.boolean().default(false),
        },
        async ({ severity, hours, limit, anomalies_only }) => {
            const params = new URLSearchParams({ severity, limit: String(limit), anomalies_only: String(anomalies_only) });
            if (hours) params.set("hours", String(hours));
            let data;
            try {
                const res = await fetch(`${FLASK_URL}/plugin/anomalies?${params}`, { signal: AbortSignal.timeout(5000) });
                data = await res.json();
            } catch (e) {
                return { content: [{ type: "text", text: `Cannot reach Flask logger: ${e.message}` }] };
            }
            const summary = data.summary || {};
            const records = data.records || [];
            const lines = [
                "## Anomaly Log Summary",
                `- Total: ${summary.total_records ?? 0}`,
                `- High: ${summary.high_severity ?? 0}  Medium: ${summary.medium_severity ?? 0}  Low: ${summary.low_severity ?? 0}`,
                "",
            ];
            if (!records.length) {
                lines.push("No records found.");
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

    s.tool(
        "get_system_health",
        "Get live health status of all FaceNet-Node services: Flask logger, YOLO server, FaceNet API, and live capture. Use when asked if the system is running or healthy.",
        {},
        async () => {
            const services = {};

            try {
                await fetch(`${FLASK_URL}/api/health`, { signal: AbortSignal.timeout(2000) });
                let records = 0;
                try { records = (await (await fetch(`${FLASK_URL}/api/stats`, { signal: AbortSignal.timeout(2000) })).json()).total_records ?? 0; } catch { }
                services.flask_logger = { status: "online", port: 5000, records };
            } catch { services.flask_logger = { status: "OFFLINE", port: 5000 }; }

            try {
                const d = await (await fetch(`${YOLO_URL}/health`, { signal: AbortSignal.timeout(2000) })).json();
                services.yolo_server = { status: "online", port: 8000, model: d.yolo, llm: d.llm, ws_clients: d.clients ?? 0 };
            } catch { services.yolo_server = { status: "OFFLINE", port: 8000 }; }

            try {
                const users = await (await fetch(`${FACENET_URL}/api/users`, { signal: AbortSignal.timeout(2000) })).json();
                services.facenet_api = { status: "online", port: 3001, registered_users: Array.isArray(users) ? users.length : 0 };
            } catch { services.facenet_api = { status: "OFFLINE", port: 3001 }; }

            let capturePid = null;
            try {
                const procs = execSync('wmic process where "name=\'python.exe\'" get CommandLine,ProcessId /FORMAT:CSV', { encoding: "utf8" });
                if (procs.includes("live_capture.py")) {
                    const match = procs.match(/live_capture[^\n,]*,(\d+)/);
                    capturePid = match ? match[1] : "running";
                }
            } catch { }
            services.live_capture = { status: capturePid ? `running (PID ${capturePid})` : "stopped" };

            const online = Object.values(services).filter(s => s.status.includes("online") || s.status.includes("running")).length;
            const lines = [`## FaceNet-Node Health — ${online}/${Object.keys(services).length} online`, `Checked: ${new Date().toLocaleString()}`, ""];
            for (const [name, info] of Object.entries(services)) {
                const icon = info.status.includes("online") || info.status.includes("running") ? "OK" : info.status === "stopped" ? "WARN" : "OFFLINE";
                lines.push(`[${icon}] ${name} — ${info.status}`);
                for (const [k, v] of Object.entries(info)) if (k !== "status") lines.push(`   ${k}: ${v}`);
            }
            if (online < Object.keys(services).length) lines.push("\nSome services offline — run start-facenet.ps1.");
            return { content: [{ type: "text", text: lines.join("\n") }] };
        }
    );

    s.tool(
        "control_capture",
        "Start or stop the FaceNet-Node live capture pipeline. Use when asked to start/stop monitoring or the camera.",
        { action: z.enum(["start", "stop"]) },
        async ({ action }) => {
            if (action === "start") {
                try {
                    const procs = execSync('wmic process where "name=\'python.exe\'" get CommandLine /FORMAT:CSV', { encoding: "utf8" });
                    if (procs.includes("live_capture.py")) return { content: [{ type: "text", text: "Live capture is already running." }] };
                } catch { }
                const child = spawn(PYTHON, [CAPTURE_PY], { detached: true, stdio: "ignore", windowsHide: false });
                child.unref();
                return { content: [{ type: "text", text: `Live capture started (PID ${child.pid}).` }] };
            }
            if (action === "stop") {
                try {
                    execSync(`wmic process where "CommandLine like '%live_capture.py%' and name='python.exe'" delete`, { encoding: "utf8" });
                    return { content: [{ type: "text", text: "Live capture stopped." }] };
                } catch { }
                return { content: [{ type: "text", text: "Live capture was not running." }] };
            }
            return { content: [{ type: "text", text: "Error: action must be start or stop." }] };
        }
    );
}

// ─── Express — fresh McpServer per request ────────────────
const app = express();
app.use(express.json());

app.all("/mcp", async (req, res) => {
    try {
        const requestServer = new McpServer({ name: "facenet-node-security", version: "1.0.0" });
        registerTools(requestServer);
        const transport = new StreamableHTTPServerTransport({ sessionIdGenerator: undefined });
        res.on("close", () => transport.close());
        await requestServer.connect(transport);
        await transport.handleRequest(req, res, req.body);
    } catch (err) {
        console.error("MCP request error:", err.message);
        if (!res.headersSent) res.status(500).json({ error: err.message });
    }
});

app.listen(PORT, () => {
    console.log(`FaceNet MCP server running at http://localhost:${PORT}/mcp`);
});