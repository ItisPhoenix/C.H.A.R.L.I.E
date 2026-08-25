import { useEffect, useRef, type ReactElement } from "react";
import { useCharlieStore } from "../../store/charlie";
import { OuterHudSystem } from "./OuterHudSystem";

type CoreVisualState =
  | "idle"
  | "listening"
  | "thinking"
  | "working"
  | "speaking"
  | "waiting"
  | "attention"
  | "completed"
  | "error"
  | "offline";

interface CoreProfile {
  energy: number;
  motion: number;
  accent: string;
  electric: string;
  hot: string;
  deep: string;
  glow: string;
}

interface SpherePoint {
  x: number;
  y: number;
  z: number;
  size: number;
  alpha: number;
}

const TWO_PI = Math.PI * 2;

const PROFILES: Record<CoreVisualState, CoreProfile> = {
  idle: {
    energy: 0.85,
    motion: 0.15,
    accent: "#00c8f8",
    electric: "#00f0ff",
    hot: "#ffffff",
    deep: "#01040a",
    glow: "rgba(0, 220, 255, 0.55)",
  },
  listening: {
    energy: 0.98,
    motion: 0.35,
    accent: "#00f0ff",
    electric: "#38bdf8",
    hot: "#ffffff",
    deep: "#020a16",
    glow: "rgba(0, 240, 255, 0.75)",
  },
  thinking: {
    energy: 0.92,
    motion: 0.55,
    accent: "#0284c7",
    electric: "#00f0ff",
    hot: "#ffffff",
    deep: "#020e20",
    glow: "rgba(2, 132, 199, 0.7)",
  },
  working: {
    energy: 1.0,
    motion: 0.5,
    accent: "#00b4d8",
    electric: "#00f0ff",
    hot: "#ffffff",
    deep: "#021226",
    glow: "rgba(0, 180, 216, 0.75)",
  },
  speaking: {
    energy: 0.95,
    motion: 0.3,
    accent: "#2dd4bf",
    electric: "#00f0ff",
    hot: "#ffffff",
    deep: "#011216",
    glow: "rgba(45, 212, 191, 0.7)",
  },
  waiting: {
    energy: 0.75,
    motion: 0.1,
    accent: "#0ea5e9",
    electric: "#38bdf8",
    hot: "#e0f2fe",
    deep: "#010814",
    glow: "rgba(14, 165, 233, 0.5)",
  },
  attention: {
    energy: 0.98,
    motion: 0.4,
    accent: "#f59e0b",
    electric: "#fbbf24",
    hot: "#fef3c7",
    deep: "#180c02",
    glow: "rgba(245, 158, 11, 0.75)",
  },
  completed: {
    energy: 1.0,
    motion: 0.6,
    accent: "#10b981",
    electric: "#34d399",
    hot: "#ecfdf5",
    deep: "#01160e",
    glow: "rgba(16, 185, 129, 0.8)",
  },
  error: {
    energy: 0.9,
    motion: 0.25,
    accent: "#f87171",
    electric: "#fca5a5",
    hot: "#fef2f2",
    deep: "#1a0404",
    glow: "rgba(248, 113, 113, 0.7)",
  },
  offline: {
    energy: 0.5,
    motion: 0.02,
    accent: "#64748b",
    electric: "#94a3b8",
    hot: "#cbd5e1",
    deep: "#080c14",
    glow: "rgba(100, 116, 139, 0.35)",
  },
};

function normalizeState(rawState: string, connected: boolean): CoreVisualState {
  if (!connected) return "offline";
  if (rawState === "executing") return "working";
  if (rawState in PROFILES) return rawState as CoreVisualState;
  return "idle";
}

function colorWithAlpha(color: string, alpha: number): string {
  const hex = color.replace("#", "");
  const value = Number.parseInt(hex, 16);
  return `rgba(${value >> 16}, ${(value >> 8) & 255}, ${value & 255}, ${Math.max(0, Math.min(1, alpha))})`;
}

function generateSpherePoints(count = 360): SpherePoint[] {
  const points: SpherePoint[] = [];
  const phi = Math.PI * (3 - Math.sqrt(5)); // Golden ratio angle

  for (let i = 0; i < count; i++) {
    const y = 1 - (i / (count - 1)) * 2;
    const radiusAtY = Math.sqrt(1 - y * y);
    const theta = phi * i;

    const x = Math.cos(theta) * radiusAtY;
    const z = Math.sin(theta) * radiusAtY;
    const layer = (i % 3) * 0.08;
    const r = 0.84 + layer;

    points.push({
      x: x * r,
      y: y * r,
      z: z * r,
      size: i % 4 === 0 ? 1.3 : 0.85,
      alpha: 0.28 + (i % 5) * 0.12,
    });
  }
  return points;
}

const SPHERE_POINTS = generateSpherePoints(360);

/**
 * 1. Deep Dark Central Disk — pitch black center with subtle radial depth.
 */
function drawInnerDisk(
  ctx: CanvasRenderingContext2D,
  centerX: number,
  centerY: number,
  diskRadius: number,
  profile: CoreProfile
): void {
  ctx.save();
  const grad = ctx.createRadialGradient(centerX, centerY, 0, centerX, centerY, diskRadius);
  grad.addColorStop(0, "#01040a");
  grad.addColorStop(0.7, "#01050e");
  grad.addColorStop(0.94, "#020b18");
  grad.addColorStop(1, colorWithAlpha(profile.accent, 0.18));
  ctx.fillStyle = grad;
  ctx.beginPath();
  ctx.arc(centerX, centerY, diskRadius, 0, TWO_PI);
  ctx.fill();
  ctx.restore();
}

/**
 * 2. Subtle Volumetric Dotted Particle Sphere inside the black disk behind C.H.A.R.L.I.E.
 */
function drawParticleSphere(
  ctx: CanvasRenderingContext2D,
  centerX: number,
  centerY: number,
  sphereRadius: number,
  time: number,
  profile: CoreProfile,
  points: SpherePoint[],
  reduceMotion: boolean
): void {
  const rotY = reduceMotion ? 0.3 : time * 0.00045 * (1 + profile.motion * 0.4);
  const rotX = reduceMotion ? 0.2 : Math.sin(time * 0.0003) * 0.25;

  const cosY = Math.cos(rotY);
  const sinY = Math.sin(rotY);
  const cosX = Math.cos(rotX);
  const sinX = Math.sin(rotX);

  ctx.save();
  for (let i = 0; i < points.length; i++) {
    const p = points[i];

    // 3D rotation Y
    const x1 = p.x * cosY + p.z * sinY;
    const z1 = -p.x * sinY + p.z * cosY;

    // 3D rotation X
    const y2 = p.y * cosX - z1 * sinX;
    const z2 = p.y * sinX + z1 * cosX;

    // Perspective projection
    const perspective = 1 / (1.25 - z2 * 0.22);
    const px = centerX + x1 * sphereRadius * perspective;
    const py = centerY + y2 * sphereRadius * perspective;

    // Soft center mask: keep text zone clear
    const distFromCenter = Math.hypot(px - centerX, py - centerY);
    if (distFromCenter < sphereRadius * 0.26) {
      continue;
    }

    // Depth shading: front particles brighter, rear particles dimmer
    const depthFactor = (z2 + 1) * 0.5;
    const alpha = Math.max(0.06, Math.min(0.75, p.alpha * (0.35 + depthFactor * 0.65) * profile.energy));
    const size = Math.max(0.6, p.size * perspective * (0.8 + depthFactor * 0.35));

    ctx.fillStyle = z2 > 0.25
      ? colorWithAlpha(profile.electric, alpha)
      : colorWithAlpha(profile.accent, alpha * 0.7);

    ctx.beginPath();
    ctx.arc(px, py, size, 0, TWO_PI);
    ctx.fill();
  }
  ctx.restore();
}

/**
 * 3. Luminous Electric Cyan / Blue Torus Ring with Hot Energy Crescent & Concentric Filaments
 */
function drawLuminousRing(
  ctx: CanvasRenderingContext2D,
  centerX: number,
  centerY: number,
  unit: number,
  time: number,
  profile: CoreProfile,
  pulse: number,
  audioLevel: number
): void {
  const baseRadius = unit * 0.285;
  const breath = 1 + Math.sin(time * 0.002) * 0.004 + pulse * 0.03 + audioLevel * 0.02;
  const r = baseRadius * breath;
  const rot = time * 0.0006 * (1 + profile.motion);

  ctx.save();

  // A. Broad Electric-Blue Atmospheric Falloff
  const outerHalo = ctx.createRadialGradient(
    centerX,
    centerY,
    r - unit * 0.03,
    centerX,
    centerY,
    r + unit * 0.12
  );
  outerHalo.addColorStop(0, "rgba(0, 240, 255, 0)");
  outerHalo.addColorStop(0.25, profile.glow);
  outerHalo.addColorStop(0.55, "rgba(0, 140, 255, 0.25)");
  outerHalo.addColorStop(0.85, "rgba(0, 100, 220, 0.08)");
  outerHalo.addColorStop(1, "rgba(0, 0, 0, 0)");

  ctx.fillStyle = outerHalo;
  ctx.beginPath();
  ctx.arc(centerX, centerY, r + unit * 0.12, 0, TWO_PI);
  ctx.fill();

  // B. Multi-Layer Concentric Luminous Filaments (Wisps of Light creating deep torus texture)
  ctx.lineCap = "round";
  const filaments = [
    { offset: -unit * 0.018, width: 1.0, alpha: 0.45, speed: 0.8, start: 0.2, len: 1.6, color: "#00f0ff" },
    { offset: -unit * 0.01, width: 1.4, alpha: 0.6, speed: -0.6, start: 0.8, len: 1.8, color: "#38bdf8" },
    { offset: unit * 0.008, width: 1.2, alpha: 0.55, speed: 1.1, start: 1.4, len: 1.7, color: "#00d4ff" },
    { offset: unit * 0.016, width: 1.0, alpha: 0.45, speed: -0.9, start: 0.5, len: 1.4, color: "#00f0ff" },
    { offset: unit * 0.024, width: 0.8, alpha: 0.35, speed: 0.5, start: 1.1, len: 1.3, color: "#0284c7" },
  ];

  for (let i = 0; i < filaments.length; i++) {
    const f = filaments[i];
    const fr = r + f.offset;
    const startAngle = rot * f.speed + f.start * Math.PI;
    const arcLength = Math.PI * f.len;

    ctx.strokeStyle = colorWithAlpha(f.color, f.alpha);
    ctx.lineWidth = f.width;
    ctx.shadowColor = "#00f0ff";
    ctx.shadowBlur = unit * 0.015;
    ctx.beginPath();
    ctx.arc(centerX, centerY, fr, startAngle, startAngle + arcLength);
    ctx.stroke();
  }

  // C. Main Vivid Electric-Cyan Torus Body (Vibrant saturated cyan-blue)
  ctx.strokeStyle = profile.accent;
  ctx.lineWidth = Math.max(4.0, unit * 0.02);
  ctx.shadowColor = profile.electric;
  ctx.shadowBlur = unit * 0.035;
  ctx.beginPath();
  ctx.arc(centerX, centerY, r, 0, TWO_PI);
  ctx.stroke();

  // D. Secondary Electric Blue Glow Layer on Torus
  ctx.strokeStyle = "rgba(0, 180, 240, 0.75)";
  ctx.lineWidth = Math.max(6.0, unit * 0.03);
  ctx.shadowColor = "#00d4ff";
  ctx.shadowBlur = unit * 0.025;
  ctx.beginPath();
  ctx.arc(centerX, centerY, r, 0, TWO_PI);
  ctx.stroke();

  // E. Inner Sharp Cyan Rim (catchlight)
  ctx.strokeStyle = "rgba(180, 240, 255, 0.9)";
  ctx.lineWidth = 1.2;
  ctx.shadowColor = "#00f0ff";
  ctx.shadowBlur = 6;
  ctx.beginPath();
  ctx.arc(centerX, centerY, r - unit * 0.008, 0, TWO_PI);
  ctx.stroke();

  // F. Brilliant Top/Upper-Left Hot-White Energy Crescent Highlight
  // Crescent from ~10 o'clock to ~1:30
  ctx.strokeStyle = "rgba(0, 240, 255, 0.95)";
  ctx.lineWidth = Math.max(3.0, unit * 0.014);
  ctx.shadowColor = "#00f0ff";
  ctx.shadowBlur = unit * 0.02;
  ctx.beginPath();
  ctx.arc(centerX, centerY, r, -Math.PI * 0.85, -Math.PI * 0.05);
  ctx.stroke();

  // Hot White Core of Crescent
  ctx.strokeStyle = "#ffffff";
  ctx.lineWidth = Math.max(2.0, unit * 0.009);
  ctx.shadowColor = "#ffffff";
  ctx.shadowBlur = unit * 0.015;
  ctx.beginPath();
  ctx.arc(centerX, centerY, r, -Math.PI * 0.75, -Math.PI * 0.15);
  ctx.stroke();

  // G. Dynamic Directional Traveling Wave Segment
  const traceAngle = (time * 0.0016 * (1 + profile.motion * 0.5)) % TWO_PI;
  const traceLen = Math.PI * 0.28;
  ctx.strokeStyle = "rgba(255, 255, 255, 0.95)";
  ctx.lineWidth = Math.max(2.0, unit * 0.009);
  ctx.shadowColor = "#ffffff";
  ctx.shadowBlur = 10;
  ctx.beginPath();
  ctx.arc(centerX, centerY, r + unit * 0.004, traceAngle, traceAngle + traceLen);
  ctx.stroke();

  // H. Completion / Click Pulse Wave
  if (pulse > 0) {
    const pulseR = r + (1 - pulse) * unit * 0.18;
    ctx.strokeStyle = colorWithAlpha("#ffffff", pulse * 0.9);
    ctx.lineWidth = 2.4;
    ctx.shadowColor = "#00f0ff";
    ctx.shadowBlur = unit * 0.04;
    ctx.beginPath();
    ctx.arc(centerX, centerY, pulseR, 0, TWO_PI);
    ctx.stroke();
  }

  ctx.restore();
}

function drawFrame(
  ctx: CanvasRenderingContext2D,
  width: number,
  height: number,
  time: number,
  currentState: CoreVisualState,
  audioLevel: number,
  pulseStartedAt: number,
  clickPulseStartedAt: number,
  reduceMotion: boolean
): void {
  const profile = PROFILES[currentState] || PROFILES.idle;
  const centerX = width / 2;
  const centerY = height / 2;
  const unit = Math.min(width, height);
  const elapsed = reduceMotion ? 0 : time;

  const statePulse =
    currentState === "completed"
      ? Math.max(0, Math.min(1, (time - pulseStartedAt) / 800))
      : 0;
  const clickPulse = Math.max(0, Math.min(1, (time - clickPulseStartedAt) / 500));
  const pulse = Math.max(statePulse > 0 ? 1 - statePulse : 0, clickPulse > 0 ? 1 - clickPulse : 0);

  ctx.clearRect(0, 0, width, height);

  // 1. Deep pitch-black circular disk
  drawInnerDisk(ctx, centerX, centerY, unit * 0.28, profile);

  // 2. Subtle volumetric dotted particle sphere inside black disk
  drawParticleSphere(ctx, centerX, centerY, unit * 0.23, elapsed, profile, SPHERE_POINTS, reduceMotion);

  // 3. Exact multi-layer luminous electric cyan torus ring with hot crescent
  drawLuminousRing(ctx, centerX, centerY, unit, elapsed, profile, pulse, audioLevel);
}

export function CharlieRing(): ReactElement {
  const coreState = useCharlieStore((state) => state.coreState);
  const connected = useCharlieStore((state) => state.connected);

  const canvasRef = useRef<HTMLCanvasElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const stateRef = useRef<CoreVisualState>(normalizeState(coreState, connected));
  const lastStateRef = useRef<CoreVisualState>(stateRef.current);
  const audioLevelRef = useRef(0);
  const pulseStartedAtRef = useRef(0);
  const clickPulseStartedAtRef = useRef(0);
  const reduceMotionRef = useRef(false);

  const state = normalizeState(coreState, connected);

  useEffect(() => {
    const previousState = lastStateRef.current;
    stateRef.current = state;
    lastStateRef.current = state;
    if (state !== previousState && state === "completed") {
      pulseStartedAtRef.current = performance.now();
    }
  }, [state]);

  // Transient subscription: audio levels update ref for canvas loop and DOM attribute directly without React component re-renders
  useEffect(() => {
    if (containerRef.current) {
      containerRef.current.setAttribute("data-audio-level", String(useCharlieStore.getState().audioLevel));
    }
    const unsub = useCharlieStore.subscribe((state) => {
      audioLevelRef.current = state.audioLevel;
      if (containerRef.current) {
        containerRef.current.setAttribute("data-audio-level", String(state.audioLevel));
      }
    });
    return unsub;
  }, []);

  useEffect(() => {
    const canvas = canvasRef.current;
    const container = containerRef.current;
    if (!canvas || !container) return;
    const context = canvas.getContext("2d", { alpha: true });
    if (!context) return;

    let width = 1;
    let height = 1;
    let animationFrame = 0;
    let stopped = false;

    const resize = () => {
      const bounds = container.getBoundingClientRect();
      const ratio = Math.min(window.devicePixelRatio || 1, 2);
      width = Math.max(1, bounds.width);
      height = Math.max(1, bounds.height);
      canvas.width = Math.floor(width * ratio);
      canvas.height = Math.floor(height * ratio);
      canvas.style.width = `${width}px`;
      canvas.style.height = `${height}px`;
      context.setTransform(ratio, 0, 0, ratio, 0, 0);
    };

    const observer = new ResizeObserver(resize);
    observer.observe(container);
    resize();

    const mediaQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
    reduceMotionRef.current = mediaQuery.matches;
    const onReducedMotionChange = (event: MediaQueryListEvent) => {
      reduceMotionRef.current = event.matches;
    };
    mediaQuery.addEventListener("change", onReducedMotionChange);

    const render = (timestamp: number) => {
      if (stopped) return;
      if (!document.hidden) {
        drawFrame(
          context,
          width,
          height,
          timestamp,
          stateRef.current,
          audioLevelRef.current,
          pulseStartedAtRef.current,
          clickPulseStartedAtRef.current,
          reduceMotionRef.current
        );
      }
      animationFrame = window.requestAnimationFrame(render);
    };
    animationFrame = window.requestAnimationFrame(render);

    return () => {
      stopped = true;
      window.cancelAnimationFrame(animationFrame);
      observer.disconnect();
      mediaQuery.removeEventListener("change", onReducedMotionChange);
    };
  }, []);

  const handleClick = () => {
    clickPulseStartedAtRef.current = performance.now();
  };

  const label = state === "offline" ? "Offline" : state;

  return (
    <div
      ref={containerRef}
      className="hud-ring"
      data-core-renderer="authoritative-charlie-ring"
      data-state={state}
      data-audio-level={useCharlieStore.getState().audioLevel}
      role="img"
      aria-label={`Charlie ${label}`}
      onClick={handleClick}
    >
      <canvas ref={canvasRef} className="hud-core-canvas" aria-hidden="true" />
      <OuterHudSystem />
    </div>
  );
}
