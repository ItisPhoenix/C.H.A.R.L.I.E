"use client";

import { useEffect, useRef, useState, type ReactElement } from "react";
import {
  Activity, AlertTriangle, ListTree, ShieldAlert, WifiOff,
} from "lucide-react";
import { useCharlieStore, type VoiceState } from "../store/useCharlieStore";

const STATE_COLOR: Record<VoiceState, string> = {
  idle: "#4b5563",
  listening: "#06b6d4",
  thinking: "#a855f7",
  speaking: "#10b981",
};

const STATE_LABEL: Record<VoiceState, string> = {
  idle: "Idle",
  listening: "Listening",
  thinking: "Thinking",
  speaking: "Speaking",
};

interface Point3 {
  x: number;
  y: number;
  z: number;
}

const PARTICLE_COUNT = 640;
const NEIGHBORS_PER_POINT = 2;

function hexToRgb(hex: string): [number, number, number] {
  const n = parseInt(hex.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

const STATE_RGB: Record<VoiceState, [number, number, number]> = {
  idle: hexToRgb(STATE_COLOR.idle),
  listening: hexToRgb(STATE_COLOR.listening),
  thinking: hexToRgb(STATE_COLOR.thinking),
  speaking: hexToRgb(STATE_COLOR.speaking),
};

const lerp = (a: number, b: number, t: number) => a + (b - a) * t;

// Quantize alpha into tiers so each tier paints as one path instead of one canvas call per item -- the real fix for the frame lag.
const ALPHA_BUCKETS = 6;
const bucketIndex = (alpha: number, max: number): number =>
  Math.min(ALPHA_BUCKETS - 1, Math.max(0, Math.floor((alpha / max) * ALPHA_BUCKETS)));

/** Fibonacci-spiral sphere -- evenly distributed points via the golden angle. */
function buildSphere(count: number): Point3[] {
  const points: Point3[] = [];
  const golden = Math.PI * (3 - Math.sqrt(5));
  for (let i = 0; i < count; i++) {
    const y = 1 - (i / (count - 1)) * 2;
    const radius = Math.sqrt(1 - y * y);
    const theta = golden * i;
    points.push({ x: Math.cos(theta) * radius, y, z: Math.sin(theta) * radius });
  }
  return points;
}

const SPHERE = buildSphere(PARTICLE_COUNT);

/** Connect each point to its K nearest neighbors -- gives the wireframe/constellation look. */
function buildEdges(points: Point3[], k: number): [number, number][] {
  const edges: [number, number][] = [];
  const seen = new Set<string>();
  for (let i = 0; i < points.length; i++) {
    const distances: { j: number; d: number }[] = [];
    for (let j = 0; j < points.length; j++) {
      if (i === j) continue;
      const dx = points[i].x - points[j].x;
      const dy = points[i].y - points[j].y;
      const dz = points[i].z - points[j].z;
      distances.push({ j, d: dx * dx + dy * dy + dz * dz });
    }
    distances.sort((a, b) => a.d - b.d);
    for (const { j } of distances.slice(0, k)) {
      const key = i < j ? `${i}-${j}` : `${j}-${i}`;
      if (!seen.has(key)) {
        seen.add(key);
        edges.push([i, j]);
      }
    }
  }
  return edges;
}

/** Deterministic PRNG (mulberry32) -- same tangled-chord layout every load. */
function mulberry32(seed: number): () => number {
  let a = seed;
  return () => {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Long random chords through the sphere's interior -- gives the tangled-energy look instead of a clean geodesic wireframe. */
function buildChords(count: number, total: number): [number, number][] {
  const rand = mulberry32(1337);
  const chords: [number, number][] = [];
  for (let c = 0; c < count; c++) {
    chords.push([Math.floor(rand() * total), Math.floor(rand() * total)]);
  }
  return chords;
}

const EDGES = [...buildEdges(SPHERE, NEIGHBORS_PER_POINT), ...buildChords(220, PARTICLE_COUNT)];

/** Ambient orb centerpiece -- hand-rolled Canvas 2D + rAF, driven by voiceState + live audioLevel. */
function Orb(): ReactElement {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const voiceState = useCharlieStore((s) => s.voiceState);
  const audioLevel = useCharlieStore((s) => s.audioLevel);
  const stateRef = useRef(voiceState);
  const levelRef = useRef(audioLevel);

  useEffect(() => {
    stateRef.current = voiceState;
    levelRef.current = audioLevel;
  }, [voiceState, audioLevel]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    let width = 0;
    let height = 0;

    const resize = () => {
      const rect = canvas.getBoundingClientRect();
      width = rect.width;
      height = rect.height;
      canvas.width = width * dpr;
      canvas.height = height * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    resize();
    window.addEventListener("resize", resize);

    let angle = 0;
    let time = 0;
    let raf = 0;
    let running = true;
    // Eased toward each frame so a state change breathes into place instead of snapping.
    let curSpeed = 0.003;
    let curTurbulence = 0;
    let curPulse = 0;
    let curRgb: [number, number, number] = [...STATE_RGB.idle];

    const draw = () => {
      const state = stateRef.current;
      const level = levelRef.current;
      const targetRgb = STATE_RGB[state];
      const cx = width / 2;
      const cy = height / 2;
      const baseRadius = Math.min(width, height) * 0.44;

      const idleBreath = state === "idle" ? Math.sin(time * 0.6) * 0.02 : 0;
      const targetSpeed = state === "thinking" ? 0.024 : state === "idle" ? 0.003 : 0.01;
      // Small on purpose -- must stay well under the ~1.6 depth baseline or the sphere warps into a bell/diamond shape.
      const targetTurbulence = state === "thinking" ? 0.05 + Math.sin(time * 2.2) * 0.015 : 0;
      // Speaking breathes bigger and faster than listening's tighter attentive pulse.
      const targetPulse =
        state === "speaking" ? level * 0.55 + Math.sin(time * 8) * 0.03 * level
        : state === "listening" ? level * 0.28
        : idleBreath;

      const ease = 0.08;
      curSpeed = lerp(curSpeed, targetSpeed, ease);
      curTurbulence = lerp(curTurbulence, targetTurbulence, ease);
      curPulse = lerp(curPulse, targetPulse, ease);
      curRgb = [
        lerp(curRgb[0], targetRgb[0], ease),
        lerp(curRgb[1], targetRgb[1], ease),
        lerp(curRgb[2], targetRgb[2], ease),
      ];
      const color = `rgb(${curRgb[0] | 0}, ${curRgb[1] | 0}, ${curRgb[2] | 0})`;
      const radius = baseRadius * (1 + curPulse);

      ctx.clearRect(0, 0, width, height);

      // Speaking gets a soft amplitude-driven halo -- the clearest visual tell it's talking, not just listening.
      if (state === "speaking") {
        const haloR = radius * (1.15 + level * 0.25);
        const gradient = ctx.createRadialGradient(cx, cy, radius * 0.4, cx, cy, haloR);
        gradient.addColorStop(0, `rgba(${curRgb[0] | 0}, ${curRgb[1] | 0}, ${curRgb[2] | 0}, ${0.12 + level * 0.15})`);
        gradient.addColorStop(1, "rgba(0, 0, 0, 0)");
        ctx.fillStyle = gradient;
        ctx.fillRect(0, 0, width, height);
      }

      // Outer scan ring -- one smooth dashed circle instead of 48 separate segments (was flickery).
      ctx.globalCompositeOperation = "source-over";
      ctx.save();
      ctx.translate(cx, cy);
      ctx.rotate(angle * 0.35);
      ctx.strokeStyle = `rgba(${curRgb[0] | 0}, ${curRgb[1] | 0}, ${curRgb[2] | 0}, 0.32)`;
      ctx.lineWidth = 1.25;
      ctx.setLineDash([2, 10]);
      ctx.beginPath();
      ctx.arc(0, 0, radius * 1.32, 0, Math.PI * 2);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.restore();

      // Project every point once for both mesh and particles; turbulence phase uses x/y/z (not just y) so it shimmers instead of dragging whole latitude bands out of shape.
      const projected = SPHERE.map((p) => {
        const wobble = curTurbulence
          ? Math.sin(time * 3 + p.x * 8 + p.y * 5 + p.z * 6) * curTurbulence
          : 0;
        const rx = p.x * Math.cos(angle) - p.z * Math.sin(angle);
        const rz = p.x * Math.sin(angle) + p.z * Math.cos(angle);
        const depth = rz + 1.6 + wobble;
        const scale = 1 / depth;
        return { px: cx + rx * radius * scale, py: cy + p.y * radius * scale, scale };
      });

      // Bucket into alpha tiers, one path per tier -- ~2000 individual canvas calls/frame was the actual lag source.
      ctx.globalCompositeOperation = "lighter";
      ctx.strokeStyle = color;
      ctx.lineWidth = 0.6;
      const edgeBuckets: Array<Array<[number, number, number, number]>> = Array.from({ length: ALPHA_BUCKETS }, () => []);
      for (const [i, j] of EDGES) {
        const a = projected[i];
        const b = projected[j];
        const avgScale = (a.scale + b.scale) / 2;
        const alpha = Math.max(0, Math.min(0.35, avgScale * 0.22));
        if (alpha <= 0.01) continue;
        edgeBuckets[bucketIndex(alpha, 0.35)].push([a.px, a.py, b.px, b.py]);
      }
      for (let bi = 0; bi < ALPHA_BUCKETS; bi++) {
        const segs = edgeBuckets[bi];
        if (segs.length === 0) continue;
        ctx.globalAlpha = ((bi + 0.5) / ALPHA_BUCKETS) * 0.35;
        const path = new Path2D();
        for (const [ax, ay, bx, by] of segs) {
          path.moveTo(ax, ay);
          path.lineTo(bx, by);
        }
        ctx.stroke(path);
      }

      ctx.fillStyle = color;
      const particleBuckets: Array<Array<[number, number, number]>> = Array.from({ length: ALPHA_BUCKETS }, () => []);
      for (const { px, py, scale } of projected) {
        const alpha = Math.max(0.08, Math.min(0.9, scale * 0.55));
        const size = Math.max(0.6, scale * 1.8);
        particleBuckets[bucketIndex(alpha, 0.9)].push([px, py, size]);
      }
      for (let bi = 0; bi < ALPHA_BUCKETS; bi++) {
        const pts = particleBuckets[bi];
        if (pts.length === 0) continue;
        ctx.globalAlpha = ((bi + 0.5) / ALPHA_BUCKETS) * 0.9;
        const path = new Path2D();
        for (const [px, py, size] of pts) {
          path.moveTo(px + size, py);
          path.arc(px, py, size, 0, Math.PI * 2);
        }
        ctx.fill(path);
      }
      ctx.globalAlpha = 1;
      ctx.globalCompositeOperation = "source-over";

      if (!reduceMotion) {
        angle += curSpeed;
        time += 0.016;
      }
      if (running) raf = requestAnimationFrame(draw);
    };

    const handleVisibility = () => {
      if (document.hidden) {
        running = false;
        cancelAnimationFrame(raf);
      } else if (!running) {
        running = true;
        raf = requestAnimationFrame(draw);
      }
    };
    document.addEventListener("visibilitychange", handleVisibility);

    raf = requestAnimationFrame(draw);
    if (reduceMotion) {
      // Single static frame -- still reflects state color/size, just doesn't rotate.
      cancelAnimationFrame(raf);
      draw();
      running = false;
    }

    return () => {
      running = false;
      cancelAnimationFrame(raf);
      window.removeEventListener("resize", resize);
      document.removeEventListener("visibilitychange", handleVisibility);
    };
  }, []);

  return (
    <div className="flex-1 flex flex-col items-center justify-center min-h-0">
      <canvas ref={canvasRef} className="w-full h-full max-w-3xl max-h-[42rem]" aria-hidden="true" />
      <span
        className="font-mono text-xs uppercase tracking-[0.3em] -mt-4"
        style={{ color: STATE_COLOR[voiceState] }}
      >
        {STATE_LABEL[voiceState]}
      </span>
      <Caption />
    </div>
  );
}

// Genuinely synced: voice.py fires one speaking_start pulse per sentence with its real text, so this shows exactly what's playing.
function Caption(): ReactElement | null {
  const voiceState = useCharlieStore((s) => s.voiceState);
  const audioLevel = useCharlieStore((s) => s.audioLevel);
  const currentSpeechChunk = useCharlieStore((s) => s.currentSpeechChunk);

  // STT has no live partial transcript, only a final result -- show a plain indicator instead of stale text from the previous turn.
  if (voiceState === "listening" && audioLevel > 0.04) {
    return (
      <div className="mt-4 px-5 py-2.5 rounded-full border border-white/10 bg-zinc-950/60 backdrop-blur-sm anim-rise">
        <p className="text-xs text-slate-500 text-center font-mono uppercase tracking-wider">Listening...</p>
      </div>
    );
  }

  if (voiceState !== "speaking" || !currentSpeechChunk) return null;

  return (
    <div
      key={currentSpeechChunk}
      className="mt-4 max-w-2xl px-5 py-2.5 rounded-2xl border border-white/10 bg-zinc-950/60 backdrop-blur-sm anim-rise"
    >
      <p className="text-xs text-slate-300 text-center leading-relaxed">
        <span className="text-slate-500 uppercase font-mono text-[10px] mr-2">Charlie</span>
        {currentSpeechChunk}
      </p>
    </div>
  );
}

interface TileProps {
  label: string;
  value: string;
  accent?: string;
  wide?: boolean;
}

/** Bento-style tile -- most are 1x1, a couple of "hero" cards span both columns for a mosaic layout. */
function Tile({ label, value, accent, wide }: TileProps): ReactElement {
  return (
    <div className={`rounded-xl border border-white/5 bg-zinc-900/30 p-3.5 flex flex-col gap-1 min-w-0 ${wide ? "col-span-2" : ""}`}>
      <span className="text-[10px] font-mono uppercase tracking-wider text-slate-500 truncate">{label}</span>
      <span className="text-sm font-bold font-mono truncate" style={{ color: accent }}>{value}</span>
    </div>
  );
}

interface McpHealth {
  enabled: boolean;
  connected: boolean;
}

/** Control Center: default landing page, look-only -- everything actionable stays in CommandPalette. */
export function ControlCenterView(): ReactElement {
  const connected = useCharlieStore((s) => s.connected);
  const sessions = useCharlieStore((s) => s.sessions);
  const currentSessionId = useCharlieStore((s) => s.currentSessionId);
  const queue = useCharlieStore((s) => s.queue);
  const agentRuns = useCharlieStore((s) => s.agentRuns);
  const executionTraces = useCharlieStore((s) => s.executionTraces);
  const toolActivity = useCharlieStore((s) => s.toolActivity);
  const activeProposal = useCharlieStore((s) => s.activeProposal);
  const activeToolApproval = useCharlieStore((s) => s.activeToolApproval);

  const [mcpHealth, setMcpHealth] = useState<McpHealth | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function poll() {
      try {
        const res = await fetch("/api/mcp/status");
        if (res.ok && !cancelled) setMcpHealth(await res.json());
      } catch {
        // Own tile shows "unknown" below; not worth a toast for a background poll.
      }
    }
    poll();
    const interval = setInterval(poll, 10000);
    return () => { cancelled = true; clearInterval(interval); };
  }, []);

  const activeSession = sessions.find((s) => s.id === currentSessionId);
  const toolCallsThisSession = Object.values(executionTraces).reduce((sum, entries) => sum + entries.length, 0);
  const runningAgents = agentRuns.filter((r) => r.status === "running").length;
  const mcpLabel = mcpHealth === null ? "Unknown" : !mcpHealth.enabled ? "Off" : mcpHealth.connected ? "Connected" : "Disconnected";
  const mcpColor = mcpHealth?.connected ? "var(--color-status-success)" : mcpHealth?.enabled ? "var(--color-status-error)" : "var(--color-status-idle)";

  const recentActivity = toolActivity.slice(-6).reverse();

  return (
    <div className="flex-1 flex flex-col p-6 gap-4 overflow-hidden anim-rise">
      {/* Tier 1: only rendered when something actually needs the user */}
      {(!connected || activeProposal || activeToolApproval) && (
        <div className="shrink-0 flex flex-col gap-2">
          {!connected && (
            <div className="flex items-center gap-2 rounded-lg border border-status-error/30 bg-status-error-dim px-3 py-2 text-xs text-red-200">
              <WifiOff className="w-4 h-4 shrink-0 text-status-error" />
              Backend connection lost -- reconnecting...
            </div>
          )}
          {activeProposal && (
            <div className="flex items-center gap-2 rounded-lg border border-orange-500/30 bg-orange-950/20 px-3 py-2 text-xs text-orange-200">
              <AlertTriangle className="w-4 h-4 shrink-0" />
              Command recovery proposal waiting on you in Chats.
            </div>
          )}
          {activeToolApproval && (
            <div className="flex items-center gap-2 rounded-lg border border-status-warning/30 bg-status-warning-dim px-3 py-2 text-xs text-amber-200">
              <ShieldAlert className="w-4 h-4 shrink-0 text-status-warning" />
              A restricted tool call needs your approval in Chats.
            </div>
          )}
        </div>
      )}

      {/* Tier 2: centerpiece + in-flight metrics */}
      <div className="flex-1 flex gap-6 min-h-0">
        <Orb />

        <div className="w-72 shrink-0 flex flex-col gap-3 overflow-y-auto scrollbar">
          <div className="grid grid-cols-2 gap-3">
            <Tile wide label="Active Session" value={activeSession?.title || "None"} />
            <Tile label="Queue Depth" value={String(queue.count)} accent={queue.count > 0 ? "var(--color-status-warning)" : undefined} />
            <Tile label="Tool Calls" value={String(toolCallsThisSession)} />
            <Tile label="Running Agents" value={String(runningAgents)} accent={runningAgents > 0 ? "var(--color-status-listening)" : undefined} />
            <Tile label="MCP Servers" value={mcpLabel} accent={mcpColor} />
            <Tile wide label="Backend" value={connected ? "Online" : "Offline"} accent={connected ? "var(--color-status-success)" : "var(--color-status-error)"} />
          </div>

          {/* Tier 3: quiet digest, not a full log */}
          <div className="flex-1 min-h-0 rounded-xl border border-white/5 bg-zinc-900/20 p-3 flex flex-col gap-2 overflow-hidden">
            <span className="text-[10px] font-mono uppercase tracking-wider text-slate-500 flex items-center gap-1.5 shrink-0">
              <ListTree className="w-3 h-3" />
              Recent Activity
            </span>
            <div className="flex-1 overflow-y-auto scrollbar space-y-1.5">
              {recentActivity.length === 0 ? (
                <p className="text-[11px] text-slate-600 italic font-mono">Nothing yet this turn.</p>
              ) : (
                recentActivity.map((entry, i) => (
                  <div key={i} className="flex items-start gap-1.5 text-[11px] font-mono text-slate-400">
                    <Activity className="w-3 h-3 mt-0.5 shrink-0 text-slate-600" />
                    <span className="truncate"><span className="text-slate-300">{entry.name}</span> {entry.text.slice(0, 60)}</span>
                  </div>
                ))
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
