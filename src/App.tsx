import React, { useEffect, useRef, useState } from 'react';
import { 
  Camera, 
  UserPlus, 
  History, 
  ShieldCheck, 
  User, 
  CheckCircle2, 
  AlertCircle,
  Loader2,
  Scan,
  Database as DbIcon,
  ShieldAlert
} from 'lucide-react';
import { motion, AnimatePresence } from 'motion/react';
import * as faceapi from '@vladmandic/face-api';
import { FaceService } from './lib/face-service';
import { cn } from './lib/utils';
import { GoogleGenAI } from "@google/genai";

interface UserData {
  id: number;
  name: string;
  descriptor: number[];
  created_at: string;
}

interface AttendanceLog {
  id: number;
  user_id: number;
  name: string;
  timestamp: string;
}

export default function App() {
  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [isModelLoaded, setIsModelLoaded] = useState(false);
  const [users, setUsers] = useState<UserData[]>([]);
  const [logs, setLogs] = useState<AttendanceLog[]>([]);
  const [activeTab, setActiveTab] = useState<'scan' | 'register' | 'logs'>('scan');
  const [isRegistering, setIsRegistering] = useState(false);
  const [newName, setNewName] = useState('');
  const [lastDetected, setLastDetected] = useState<string | null>(null);
  const [geminiAnalysis, setGeminiAnalysis] = useState<string | null>(null);
  const [isAnalyzing, setIsAnalyzing] = useState(false);

  // Initialize
  useEffect(() => {
    const init = async () => {
      await FaceService.loadModels();
      setIsModelLoaded(true);
      fetchUsers();
      fetchLogs();
    };
    init();
  }, []);

  // Camera Setup
  useEffect(() => {
    if (isModelLoaded && (activeTab === 'scan' || activeTab === 'register')) {
      startCamera();
    } else {
      stopCamera();
    }
  }, [isModelLoaded, activeTab]);

  const startCamera = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'user' } });
      if (videoRef.current) {
        videoRef.current.srcObject = stream;
      }
    } catch (err) {
      console.error("Error accessing camera:", err);
    }
  };

  const stopCamera = () => {
    if (videoRef.current && videoRef.current.srcObject) {
      const stream = videoRef.current.srcObject as MediaStream;
      stream.getTracks().forEach(track => track.stop());
      videoRef.current.srcObject = null;
    }
  };

  const fetchUsers = async () => {
    const res = await fetch('/api/users');
    const data = await res.json();
    setUsers(data);
  };

  const fetchLogs = async () => {
    const res = await fetch('/api/attendance');
    const data = await res.json();
    setLogs(data);
  };

  // Real-time detection loop
  useEffect(() => {
    let animationId: number;
    const runDetection = async () => {
      if (videoRef.current && canvasRef.current && isModelLoaded && activeTab === 'scan') {
        const detections = await FaceService.detectFaces(videoRef.current);
        
        const displaySize = { 
          width: videoRef.current.videoWidth, 
          height: videoRef.current.videoHeight 
        };
        
        if (displaySize.width > 0) {
          faceapi.matchDimensions(canvasRef.current, displaySize);
          const resizedDetections = faceapi.resizeResults(detections, displaySize);
          
          const ctx = canvasRef.current.getContext('2d');
          if (ctx) {
            ctx.clearRect(0, 0, displaySize.width, displaySize.height);
            
            const matcher = FaceService.createMatcher(users);
            
            resizedDetections.forEach(detection => {
              const box = detection.detection.box;
              let label = 'Unknown';
              let color = '#ef4444'; // red
              
              if (matcher) {
                const bestMatch = matcher.findBestMatch(detection.descriptor);
                label = bestMatch.toString();
                
                if (bestMatch.label !== 'unknown') {
                  color = '#10b981'; // green
                  handleRecognition(bestMatch.label);
                }
              }
              
              // Draw custom box
              ctx.strokeStyle = color;
              ctx.lineWidth = 3;
              ctx.strokeRect(box.x, box.y, box.width, box.height);
              
              // Draw label
              ctx.fillStyle = color;
              ctx.font = '16px Inter';
              ctx.fillText(label, box.x, box.y > 20 ? box.y - 10 : box.y + 20);
            });
          }
        }
      }
      animationId = requestAnimationFrame(runDetection);
    };

    if (isModelLoaded && activeTab === 'scan') {
      runDetection();
    }
    
    return () => cancelAnimationFrame(animationId);
  }, [isModelLoaded, activeTab, users]);

  const handleRecognition = async (name: string) => {
    const user = users.find(u => u.name === name);
    if (user && lastDetected !== name) {
      setLastDetected(name);
      const res = await fetch('/api/attendance', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: user.id })
      });
      const data = await res.json();
      if (data.status === 'logged') {
        fetchLogs();
      }
      // Reset detection after 5 seconds
      setTimeout(() => setLastDetected(null), 5000);
    }
  };

  const handleRegister = async () => {
    if (!videoRef.current || !newName) return;
    setIsRegistering(true);
    
    try {
      const detections = await FaceService.detectFaces(videoRef.current);
      if (detections.length === 0) {
        alert("No face detected. Please look at the camera.");
        return;
      }
      
      if (detections.length > 1) {
        alert("Multiple faces detected. Only one person should be in view.");
        return;
      }

      const descriptor = Array.from(detections[0].descriptor);
      
      const res = await fetch('/api/users', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: newName, descriptor })
      });
      
      if (res.ok) {
        setNewName('');
        fetchUsers();
        setActiveTab('scan');
      }
    } finally {
      setIsRegistering(false);
    }
  };

  const runGeminiAnalysis = async () => {
    if (!videoRef.current) return;
    setIsAnalyzing(true);
    setGeminiAnalysis(null);

    try {
      const canvas = document.createElement('canvas');
      canvas.width = videoRef.current.videoWidth;
      canvas.height = videoRef.current.videoHeight;
      const ctx = canvas.getContext('2d');
      ctx?.drawImage(videoRef.current, 0, 0);
      const base64Image = canvas.toDataURL('image/jpeg').split(',')[1];

      const ai = new GoogleGenAI({ apiKey: process.env.GEMINI_API_KEY! });
      const response = await ai.models.generateContent({
        model: "gemini-2.5-flash-preview",
        contents: [
          {
            parts: [
              { text: "Analyze this person for a security log. Describe their appearance, mood, and any notable features briefly. Be professional and objective." },
              { inlineData: { mimeType: "image/jpeg", data: base64Image } }
            ]
          }
        ]
      });

      setGeminiAnalysis(response.text || "Analysis failed.");
    } catch (err) {
      console.error("Gemini Error:", err);
      setGeminiAnalysis("Error connecting to AI analysis service.");
    } finally {
      setIsAnalyzing(false);
    }
  };

  return (
    <div className="min-h-screen bg-[#0a0a0a] text-white font-sans selection:bg-emerald-500/30">
      {/* Header */}
      <header className="border-b border-white/5 bg-black/40 backdrop-blur-xl sticky top-0 z-50">
        <div className="max-w-7xl mx-auto px-6 h-20 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 bg-emerald-500 rounded-xl flex items-center justify-center shadow-[0_0_20px_rgba(16,185,129,0.3)]">
              <ShieldCheck className="text-black w-6 h-6" />
            </div>
            <div>
              <h1 className="text-xl font-semibold tracking-tight">FaceNet Node</h1>
              <p className="text-xs text-zinc-500 font-mono uppercase tracking-widest">Biometric Edge System</p>
            </div>
          </div>
          
          <nav className="flex bg-zinc-900/50 p-1 rounded-xl border border-white/5">
            {[
              { id: 'scan', icon: Scan, label: 'Scanner' },
              { id: 'register', icon: UserPlus, label: 'Register' },
              { id: 'logs', icon: History, label: 'History' },
            ].map((tab) => (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id as any)}
                className={cn(
                  "flex items-center gap-2 px-4 py-2 rounded-lg transition-all duration-200 text-sm font-medium",
                  activeTab === tab.id 
                    ? "bg-emerald-500 text-black shadow-lg" 
                    : "text-zinc-400 hover:text-white hover:bg-white/5"
                )}
              >
                <tab.icon className="w-4 h-4" />
                {tab.label}
              </button>
            ))}
          </nav>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-6 py-8">
        {!isModelLoaded ? (
          <div className="h-[60vh] flex flex-col items-center justify-center gap-4">
            <Loader2 className="w-10 h-10 text-emerald-500 animate-spin" />
            <p className="text-zinc-400 font-mono animate-pulse">Initializing Neural Engines...</p>
          </div>
        ) : (
          <div className="grid grid-cols-1 lg:grid-cols-12 gap-8">
            {/* Left Column: Camera / Form */}
            <div className="lg:col-span-8 space-y-6">
              <AnimatePresence mode="wait">
                {activeTab === 'scan' || activeTab === 'register' ? (
                  <motion.div
                    key="camera-view"
                    initial={{ opacity: 0, y: 20 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, scale: 0.95 }}
                    className="relative aspect-video bg-zinc-900 rounded-3xl overflow-hidden border border-white/10 shadow-2xl group"
                  >
                    <video
                      ref={videoRef}
                      autoPlay
                      muted
                      playsInline
                      className="w-full h-full object-cover"
                    />
                    <canvas
                      ref={canvasRef}
                      className="absolute top-0 left-0 w-full h-full"
                    />
                    
                    {/* Overlay UI */}
                    <div className="absolute inset-0 pointer-events-none border-[20px] border-black/20" />
                    <div className="absolute top-6 left-6 flex items-center gap-2 bg-black/60 backdrop-blur-md px-3 py-1.5 rounded-full border border-white/10">
                      <div className="w-2 h-2 bg-emerald-500 rounded-full animate-pulse" />
                      <span className="text-[10px] font-mono uppercase tracking-tighter">Live Feed</span>
                    </div>

                    {activeTab === 'scan' && (
                      <div className="absolute bottom-6 right-6 flex gap-3 pointer-events-auto">
                        <button
                          onClick={runGeminiAnalysis}
                          disabled={isAnalyzing}
                          className="flex items-center gap-2 bg-white/10 hover:bg-white/20 backdrop-blur-md px-4 py-2 rounded-xl border border-white/10 transition-all disabled:opacity-50"
                        >
                          {isAnalyzing ? <Loader2 className="w-4 h-4 animate-spin" /> : <ShieldAlert className="w-4 h-4 text-emerald-400" />}
                          <span className="text-xs font-medium">AI Security Check</span>
                        </button>
                      </div>
                    )}
                  </motion.div>
                ) : null}
              </AnimatePresence>

              {activeTab === 'register' && (
                <motion.div
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  className="bg-zinc-900/50 border border-white/10 rounded-3xl p-8 backdrop-blur-sm"
                >
                  <h2 className="text-xl font-semibold mb-6 flex items-center gap-2">
                    <UserPlus className="text-emerald-500" />
                    Register New Identity
                  </h2>
                  <div className="flex gap-4">
                    <input
                      type="text"
                      placeholder="Enter Full Name"
                      value={newName}
                      onChange={(e) => setNewName(e.target.value)}
                      className="flex-1 bg-black/40 border border-white/10 rounded-xl px-4 py-3 focus:outline-none focus:ring-2 focus:ring-emerald-500/50 transition-all"
                    />
                    <button
                      onClick={handleRegister}
                      disabled={isRegistering || !newName}
                      className="bg-emerald-500 hover:bg-emerald-400 text-black font-semibold px-8 py-3 rounded-xl transition-all disabled:opacity-50 flex items-center gap-2"
                    >
                      {isRegistering ? <Loader2 className="w-5 h-5 animate-spin" /> : <Camera className="w-5 h-5" />}
                      Capture & Save
                    </button>
                  </div>
                </motion.div>
              )}

              {geminiAnalysis && (
                <motion.div
                  initial={{ opacity: 0, x: -20 }}
                  animate={{ opacity: 1, x: 0 }}
                  className="bg-emerald-500/5 border border-emerald-500/20 rounded-3xl p-6"
                >
                  <div className="flex items-center gap-2 mb-3 text-emerald-400">
                    <ShieldAlert className="w-5 h-5" />
                    <h3 className="font-semibold">AI Security Assessment</h3>
                  </div>
                  <div className="text-zinc-300 text-sm leading-relaxed italic">
                    {geminiAnalysis}
                  </div>
                </motion.div>
              )}
            </div>

            {/* Right Column: Sidebar */}
            <div className="lg:col-span-4 space-y-6">
              {/* Stats / Status */}
              <div className="bg-zinc-900/50 border border-white/10 rounded-3xl p-6">
                <div className="flex items-center justify-between mb-6">
                  <h3 className="text-sm font-medium text-zinc-400 uppercase tracking-wider">System Status</h3>
                  <DbIcon className="w-4 h-4 text-emerald-500" />
                </div>
                <div className="space-y-4">
                  <div className="flex justify-between items-end">
                    <span className="text-zinc-500 text-xs">Registered Users</span>
                    <span className="text-2xl font-semibold">{users.length}</span>
                  </div>
                  <div className="h-1 bg-white/5 rounded-full overflow-hidden">
                    <div className="h-full bg-emerald-500 w-full opacity-20" />
                  </div>
                  <div className="flex justify-between items-end">
                    <span className="text-zinc-500 text-xs">Today's Logs</span>
                    <span className="text-2xl font-semibold">{logs.filter(l => new Date(l.timestamp).toDateString() === new Date().toDateString()).length}</span>
                  </div>
                </div>
              </div>

              {/* Recent Activity */}
              <div className="bg-zinc-900/50 border border-white/10 rounded-3xl p-6 flex-1 min-h-[400px] flex flex-col">
                <h3 className="text-sm font-medium text-zinc-400 uppercase tracking-wider mb-6">Live Activity</h3>
                <div className="space-y-4 overflow-y-auto max-h-[500px] pr-2 custom-scrollbar">
                  {logs.length === 0 ? (
                    <div className="flex flex-col items-center justify-center py-12 text-zinc-600">
                      <History className="w-8 h-8 mb-2 opacity-20" />
                      <p className="text-xs">No activity recorded</p>
                    </div>
                  ) : (
                    logs.map((log) => (
                      <motion.div
                        initial={{ opacity: 0, x: 20 }}
                        animate={{ opacity: 1, x: 0 }}
                        key={log.id}
                        className="flex items-center gap-4 p-3 rounded-2xl bg-white/5 border border-white/5"
                      >
                        <div className="w-10 h-10 rounded-full bg-emerald-500/10 flex items-center justify-center text-emerald-500">
                          <User className="w-5 h-5" />
                        </div>
                        <div className="flex-1 min-w-0">
                          <p className="text-sm font-medium truncate">{log.name}</p>
                          <p className="text-[10px] text-zinc-500 font-mono">
                            {new Date(log.timestamp).toLocaleTimeString()}
                          </p>
                        </div>
                        <CheckCircle2 className="w-4 h-4 text-emerald-500" />
                      </motion.div>
                    ))
                  )}
                </div>
              </div>
            </div>
          </div>
        )}
      </main>

      {/* Footer */}
      <footer className="max-w-7xl mx-auto px-6 py-8 border-t border-white/5 flex justify-between items-center text-zinc-600 text-[10px] uppercase tracking-[0.2em] font-mono">
        <div>© 2026 FaceNet Node Web</div>
        <div className="flex gap-6">
          <span className="flex items-center gap-2">
            <div className="w-1.5 h-1.5 bg-emerald-500 rounded-full" />
            Node Active
          </span>
          <span className="flex items-center gap-2">
            <div className="w-1.5 h-1.5 bg-emerald-500 rounded-full" />
            DB Synced
          </span>
        </div>
      </footer>

      <style>{`
        .custom-scrollbar::-webkit-scrollbar {
          width: 4px;
        }
        .custom-scrollbar::-webkit-scrollbar-track {
          background: transparent;
        }
        .custom-scrollbar::-webkit-scrollbar-thumb {
          background: rgba(255, 255, 255, 0.1);
          border-radius: 10px;
        }
      `}</style>
    </div>
  );
}
