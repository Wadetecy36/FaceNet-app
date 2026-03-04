import express from "express";
import { createServer as createViteServer } from "vite";
import Database from "better-sqlite3";
import path from "path";
import { fileURLToPath } from "url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const db = new Database("faces.db");

// Initialize database
db.exec(`
  CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    descriptor TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
  );

  CREATE TABLE IF NOT EXISTS attendance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users (id)
  );

  CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
  );
`);

async function startServer() {
  const app = express();
  const PORT = 3000;

  app.use(express.json({ limit: '50mb' }));

  // API Routes
  app.get("/api/users", (req, res) => {
    const users = db.prepare("SELECT * FROM users").all();
    res.json(users.map((u: any) => ({
      ...u,
      descriptor: JSON.parse(u.descriptor)
    })));
  });

  app.post("/api/users", (req, res) => {
    const { name, descriptor } = req.body;
    if (!name || !descriptor) {
      return res.status(400).json({ error: "Name and descriptor are required" });
    }
    const info = db.prepare("INSERT INTO users (name, descriptor) VALUES (?, ?)").run(name, JSON.stringify(descriptor));
    res.json({ id: info.lastInsertRowid });
  });

  app.post("/api/attendance", (req, res) => {
    const { user_id } = req.body;
    if (!user_id) {
      return res.status(400).json({ error: "User ID is required" });
    }
    
    // Check if user already logged attendance in the last 5 minutes to prevent duplicates
    const recent = db.prepare("SELECT * FROM attendance WHERE user_id = ? AND timestamp > datetime('now', '-5 minutes')").get(user_id);
    
    if (recent) {
      return res.json({ message: "Attendance already logged recently", status: "duplicate" });
    }

    db.prepare("INSERT INTO attendance (user_id) VALUES (?)").run(user_id);
    res.json({ success: true, status: "logged" });
  });

  app.get("/api/attendance", (req, res) => {
    const logs = db.prepare(`
      SELECT a.*, u.name 
      FROM attendance a 
      JOIN users u ON a.user_id = u.id 
      ORDER BY a.timestamp DESC 
      LIMIT 100
    `).all();
    res.json(logs);
  });

  // Vite middleware for development
  if (process.env.NODE_ENV !== "production") {
    const vite = await createViteServer({
      server: { middlewareMode: true },
      appType: "spa",
    });
    app.use(vite.middlewares);
  } else {
    app.use(express.static(path.join(__dirname, "dist")));
    app.get("*", (req, res) => {
      res.sendFile(path.join(__dirname, "dist", "index.html"));
    });
  }

  app.listen(PORT, "0.0.0.0", () => {
    console.log(`Server running on http://localhost:${PORT}`);
  });
}

startServer();
