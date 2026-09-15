import { useEffect, useRef, type ReactElement } from "react";
import { useCharlieStore } from "../../store/charlie";
import { OuterHudSystem } from "./OuterHudSystem";

type CoreVisualState =
  | "idle"
  | "listening"
  | "transcribing"
  | "thinking"
  | "acting"
  | "working"
  | "speaking"
  | "approval_wait"
  | "waiting"
  | "attention"
  | "success"
  | "completed"
  | "recovering"
  | "degraded"
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

interface ArcSpec {
  radius: number;
  start: number;
  end: number;
  alpha: number;
  width: number;
  color?: string;
  blur?: number;
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
    energy: 0.72,
    motion: 0.08,
    accent: "#18bce8",
    electric: "#52e8ff",
    hot: "#f5fdff",
    deep: "#01040a",
    glow: "rgba(28, 157, 210, 0.38)",
  },
  listening: {
    energy: 0.98,
    motion: 0.35,
    accent: "#00f0ff",
    electric: "#38bdf8",
    hot: "#ffffff",
    deep: "#020a16",
    glow: "rgba(0, 220, 240, 0.38)",
  },
  transcribing: {
    energy: 0.92,
    motion: 0.42,
    accent: "#38bdf8",
    electric: "#67e8f9",
    hot: "#ffffff",
    deep: "#020b18",
    glow: "rgba(56, 189, 248, 0.34)",
  },
  thinking: {
    energy: 0.92,
    motion: 0.55,
    accent: "#0284c7",
    electric: "#00f0ff",
    hot: "#ffffff",
    deep: "#020e20",
    glow: "rgba(2, 132, 199, 0.38)",
  },
  acting: {
    energy: 1,
    motion: 0.88,
    accent: "#06b6d4",
    electric: "#22d3ee",
    hot: "#ffffff",
    deep: "#021226",
    glow: "rgba(6, 182, 212, 0.42)",
  },
  working: {
    energy: 1,
    motion: 0.5,
    accent: "#00b4d8",
    electric: "#00f0ff",
    hot: "#ffffff",
    deep: "#021226",
    glow: "rgba(0, 180, 216, 0.38)",
  },
  speaking: {
    energy: 0.95,
    motion: 0.3,
    accent: "#2dd4bf",
    electric: "#00f0ff",
    hot: "#ffffff",
    deep: "#011216",
    glow: "rgba(45, 212, 191, 0.36)",
  },
  approval_wait: {
    energy: 0.7,
    motion: 0.04,
    accent: "#d99818",
    electric: "#fbbf24",
    hot: "#fef3c7",
    deep: "#180c02",
    glow: "rgba(245, 158, 11, 0.3)",
  },
  waiting: {
    energy: 0.75,
    motion: 0.1,
    accent: "#0ea5e9",
    electric: "#38bdf8",
    hot: "#e0f2fe",
    deep: "#010814",
    glow: "rgba(14, 165, 233, 0.3)",
  },
  attention: {
    energy: 0.98,
    motion: 0.4,
    accent: "#d99818",
    electric: "#fbbf24",
    hot: "#fef3c7",
    deep: "#180c02",
    glow: "rgba(245, 158, 11, 0.36)",
  },
  completed: {
    energy: 1,
    motion: 0.6,
    accent: "#10b981",
    electric: "#34d399",
    hot: "#ecfdf5",
    deep: "#01160e",
    glow: "rgba(16, 185, 129, 0.38)",
  },
  success: {
    energy: 1,
    motion: 0.18,
    accent: "#10b981",
    electric: "#34d399",
    hot: "#ecfdf5",
    deep: "#01160e",
    glow: "rgba(16, 185, 129, 0.36)",
  },
  recovering: {
    energy: 0.82,
    motion: 0.22,
    accent: "#d99818",
    electric: "#fbbf24",
    hot: "#fef3c7",
    deep: "#120b02",
    glow: "rgba(245, 158, 11, 0.3)",
  },
  error: {
    energy: 0.9,
    motion: 0.25,
    accent: "#f87171",
    electric: "#fca5a5",
    hot: "#fef2f2",
    deep: "#1a0404",
    glow: "rgba(248, 113, 113, 0.34)",
  },
  degraded: {
    energy: 0.58,
    motion: 0.06,
    accent: "#d99818",
    electric: "#94a3b8",
    hot: "#fef3c7",
    deep: "#0f0c08",
    glow: "rgba(245, 158, 11, 0.22)",
  },
  offline: {
    energy: 0.68,
    motion: 0.02,
    accent: "#0e91c5",
    electric: "#45d6f5",
    hot: "#dff8fc",
    deep: "#080c14",
    glow: "rgba(22, 137, 190, 0.24)",
  },
};

function normalizeState(rawState: string, connected: boolean): CoreVisualState {
  if (!connected) return "offline";
  if (rawState === "executing" || rawState === "working") return "acting";
  if (rawState === "completed") return "success";
  if (rawState === "waiting") return "approval_wait";
  if (rawState === "attention") return "error";
  if (rawState in PROFILES) return rawState as CoreVisualState;
  return "idle";
}

function colorWithAlpha(color: string, alpha: number): string {
  const hex = color.replace("#", "");
  const value = Number.parseInt(hex, 16);
  return `rgba(${value >> 16}, ${(value >> 8) & 255}, ${value & 255}, ${Math.max(0, Math.min(1, alpha))})`;
}

function generateSpherePoints(count = 240): SpherePoint[] {
  const points: SpherePoint[] = [];
  const phi = Math.PI * (3 - Math.sqrt(5));

  for (let i = 0; i < count; i += 1) {
    const y = 1 - (i / (count - 1)) * 2;
    const radiusAtY = Math.sqrt(1 - y * y);
    const theta = phi * i;
    const x = Math.cos(theta) * radiusAtY;
    const z = Math.sin(theta) * radiusAtY;
    const layer = (i % 3) * 0.08;

    points.push({
      x: x * (0.84 + layer),
      y: y * (0.84 + layer),
      z: z * (0.84 + layer),
      size: i % 4 === 0 ? 1.2 : 0.78,
      alpha: 0.24 + (i % 5) * 0.1,
    });
  }
  return points;
}

const SPHERE_POINTS = generateSpherePoints();

function drawLine(
  ctx: CanvasRenderingContext2D,
  x1: number,
  y1: number,
  x2: number,
  y2: number,
  color: string,
  alpha: number,
  width: number,
): void {
  ctx.save();
  ctx.strokeStyle = colorWithAlpha(color, alpha);
  ctx.lineWidth = width;
  ctx.lineCap = "butt";
  ctx.beginPath();
  ctx.moveTo(x1, y1);
  ctx.lineTo(x2, y2);
  ctx.stroke();
  ctx.restore();
}

function drawArc(
  ctx: CanvasRenderingContext2D,
  centerX: number,
  centerY: number,
  unit: number,
  spec: ArcSpec,
  profile: CoreProfile,
  offset = 0,
): void {
  ctx.save();
  ctx.strokeStyle = colorWithAlpha(spec.color || profile.accent, spec.alpha * profile.energy);
  ctx.lineWidth = Math.max(0.8, unit * spec.width);
  ctx.lineCap = "butt";
  ctx.shadowColor = spec.color || profile.electric;
  ctx.shadowBlur = Math.min(unit * 0.012, spec.blur ?? 0);
  if (spec.width < 0.004) ctx.setLineDash([unit * 0.012, unit * 0.02]);
  ctx.lineDashOffset = -offset;
  ctx.beginPath();
  ctx.arc(centerX, centerY, unit * spec.radius, spec.start + offset, spec.end + offset);
  ctx.stroke();
  ctx.restore();
}

function drawCoreField(
  ctx: CanvasRenderingContext2D,
  centerX: number,
  centerY: number,
  unit: number,
  time: number,
  profile: CoreProfile,
  compact: boolean,
  reduceMotion: boolean,
): void {
  const diskRadius = unit * (compact ? 0.22 : 0.255);
  const sphereRadius = unit * (compact ? 0.19 : 0.22);
  const gradient = ctx.createRadialGradient(centerX, centerY, 0, centerX, centerY, diskRadius);
  gradient.addColorStop(0, profile.deep);
  gradient.addColorStop(0.72, "#01050e");
  gradient.addColorStop(0.94, "#020b18");
  gradient.addColorStop(1, colorWithAlpha(profile.accent, compact ? 0.1 : 0.14));

  ctx.save();
  ctx.fillStyle = gradient;
  ctx.beginPath();
  ctx.arc(centerX, centerY, diskRadius, 0, TWO_PI);
  ctx.fill();

  ctx.strokeStyle = colorWithAlpha(profile.accent, compact ? 0.16 : 0.22);
  ctx.lineWidth = Math.max(0.7, compact ? 0.8 : 1);
  ctx.beginPath();
  ctx.arc(centerX, centerY, diskRadius, 0, TWO_PI);
  ctx.stroke();

  const reticleRadius = unit * (compact ? 0.06 : 0.07);
  ctx.strokeStyle = colorWithAlpha(profile.hot, compact ? 0.16 : 0.22);
  ctx.lineWidth = Math.max(0.8, unit * 0.0018);
  ctx.beginPath();
  ctx.arc(centerX, centerY, reticleRadius, 0, TWO_PI);
  ctx.stroke();
  drawLine(ctx, centerX - reticleRadius * 1.45, centerY, centerX - reticleRadius * 0.7, centerY, profile.electric, 0.36, 1);
  drawLine(ctx, centerX + reticleRadius * 0.7, centerY, centerX + reticleRadius * 1.45, centerY, profile.electric, 0.36, 1);
  drawLine(ctx, centerX, centerY - reticleRadius * 1.45, centerX, centerY - reticleRadius * 0.7, profile.electric, 0.28, 1);
  drawLine(ctx, centerX, centerY + reticleRadius * 0.7, centerX, centerY + reticleRadius * 1.45, profile.electric, 0.28, 1);
  ctx.restore();

  const rotY = reduceMotion ? 0.3 : time * 0.00045 * (1 + profile.motion * 0.4);
  const rotX = reduceMotion ? 0.2 : Math.sin(time * 0.0003) * 0.25;
  const cosY = Math.cos(rotY);
  const sinY = Math.sin(rotY);
  const cosX = Math.cos(rotX);
  const sinX = Math.sin(rotX);
  const stride = compact ? 4 : 1;

  ctx.save();
  for (let i = 0; i < SPHERE_POINTS.length; i += stride) {
    const point = SPHERE_POINTS[i];
    const x1 = point.x * cosY + point.z * sinY;
    const z1 = -point.x * sinY + point.z * cosY;
    const y2 = point.y * cosX - z1 * sinX;
    const z2 = point.y * sinX + z1 * cosX;
    const perspective = 1 / (1.25 - z2 * 0.22);
    const x = centerX + x1 * sphereRadius * perspective;
    const y = centerY + y2 * sphereRadius * perspective;
    const distance = Math.hypot(x - centerX, y - centerY);

    if (distance < sphereRadius * 0.28) continue;

    const depth = (z2 + 1) * 0.5;
    const alpha = Math.max(
      0.035,
      Math.min(0.42, point.alpha * (0.3 + depth * 0.58) * profile.energy * (compact ? 0.72 : 0.86)),
    );
    const size = Math.max(0.48, point.size * perspective * (0.78 + depth * 0.3));

    ctx.fillStyle = z2 > 0.25
      ? colorWithAlpha(profile.electric, alpha)
      : colorWithAlpha(profile.accent, alpha * 0.72);
    ctx.beginPath();
    ctx.arc(x, y, size, 0, TWO_PI);
    ctx.fill();
  }
  ctx.restore();
}

function drawGlowSweep(
  ctx: CanvasRenderingContext2D,
  centerX: number,
  centerY: number,
  unit: number,
  time: number,
  profile: CoreProfile,
  compact: boolean,
  reduceMotion: boolean,
  radius: number,
  active: boolean,
): void {
  const revolutionMs = compact ? 6200 : 6800;
  const phase = reduceMotion ? 0 : (time % revolutionMs) / revolutionMs;
  const centerAngle = -Math.PI / 2 + phase * TWO_PI;
  const arcLength = compact ? 0.54 : 0.62;
  const start = centerAngle - arcLength * 0.58;
  const end = centerAngle + arcLength * 0.42;
  const sweepRadius = radius - unit * (compact ? 0.004 : 0.006);

  ctx.save();
  ctx.lineCap = "round";

  // One partial bloom, followed by one crisp highlight. No complete sweep ring.
  ctx.strokeStyle = colorWithAlpha(profile.electric, active ? 0.28 : 0.2);
  ctx.lineWidth = Math.max(compact ? 3 : 4, unit * 0.014);
  ctx.shadowColor = profile.electric;
  ctx.shadowBlur = Math.min(unit * 0.024, compact ? 5 : 8);
  ctx.beginPath();
  ctx.arc(centerX, centerY, sweepRadius, start, end);
  ctx.stroke();

  ctx.strokeStyle = colorWithAlpha(profile.hot, active ? 0.92 : 0.78);
  ctx.lineWidth = Math.max(compact ? 1.3 : 1.8, unit * 0.0048);
  ctx.shadowColor = profile.hot;
  ctx.shadowBlur = compact ? 2 : 3;
  ctx.beginPath();
  ctx.arc(centerX, centerY, sweepRadius, start, end);
  ctx.stroke();
  ctx.restore();
}

function drawConcentricStructure(
  ctx: CanvasRenderingContext2D,
  centerX: number,
  centerY: number,
  unit: number,
  time: number,
  profile: CoreProfile,
  compact: boolean,
  reduceMotion: boolean,
  pulse: number,
  audioLevel: number,
  currentState: CoreVisualState,
): void {
  const audioReactive = ["listening", "transcribing", "speaking"].includes(currentState) ? audioLevel : 0;
  const isIdle = currentState === "idle";
  const active = !["idle", "approval_wait", "offline", "degraded", "error"].includes(currentState);
  const baseRadius = unit * (compact ? 0.28 : 0.32);
  const breath = 1 + Math.sin(time * 0.002) * 0.003 + pulse * 0.022 + audioReactive * 0.016;
  const radius = baseRadius * breath;
  const rotation = reduceMotion ? 0 : time * 0.0006 * (1 + profile.motion);

  ctx.save();
  ctx.globalCompositeOperation = "lighter";
  const aura = ctx.createRadialGradient(
    centerX,
    centerY,
    radius - unit * 0.035,
    centerX,
    centerY,
    radius + unit * (compact ? 0.085 : 0.1),
  );
  aura.addColorStop(0, "rgba(0, 240, 255, 0)");
  aura.addColorStop(0.18, colorWithAlpha(profile.electric, isIdle ? 0.2 : 0.24));
  aura.addColorStop(0.46, profile.glow);
  aura.addColorStop(0.76, "rgba(0, 96, 168, 0.11)");
  aura.addColorStop(1, "rgba(0, 0, 0, 0)");
  ctx.fillStyle = aura;
  ctx.beginPath();
  ctx.arc(centerX, centerY, radius + unit * (compact ? 0.085 : 0.1), 0, TWO_PI);
  ctx.fill();

  const filaments = compact
    ? [
        { offset: -0.012, width: 0.9, alpha: 0.32, speed: 0.8, start: 0.2, length: 1.24, color: "#00d4ff" },
        { offset: 0.009, width: 0.8, alpha: 0.25, speed: -0.6, start: 0.8, length: 1.38, color: "#38bdf8" },
      ]
    : [
        { offset: -0.014, width: 0.9, alpha: 0.34, speed: 0.8, start: 0.2, length: 1.34, color: "#00d4ff" },
        { offset: -0.006, width: 1.1, alpha: 0.4, speed: -0.6, start: 0.8, length: 1.46, color: "#38bdf8" },
        { offset: 0.009, width: 0.85, alpha: 0.3, speed: 1.1, start: 1.4, length: 1.3, color: "#00f0ff" },
      ];

  ctx.lineCap = "round";
  filaments.forEach((filament) => {
    ctx.strokeStyle = colorWithAlpha(filament.color, filament.alpha * (isIdle ? 0.82 : 0.94));
    ctx.lineWidth = filament.width;
    ctx.shadowColor = profile.electric;
    ctx.shadowBlur = compact ? 1.5 : 2.5;
    ctx.beginPath();
    ctx.arc(
      centerX,
      centerY,
      radius + unit * filament.offset,
      rotation * filament.speed + filament.start * Math.PI,
      rotation * filament.speed + filament.start * Math.PI + Math.PI * filament.length,
    );
    ctx.stroke();
  });

  ctx.strokeStyle = colorWithAlpha(profile.electric, isIdle ? 0.92 : 0.96);
  ctx.lineWidth = Math.max(compact ? 2.4 : 3.2, unit * (isIdle ? 0.013 : 0.014));
  ctx.shadowColor = profile.electric;
  ctx.shadowBlur = compact ? 5 : 8;
  ctx.beginPath();
  ctx.arc(centerX, centerY, radius, 0, TWO_PI);
  ctx.stroke();

  drawGlowSweep(ctx, centerX, centerY, unit, time, profile, compact, reduceMotion, radius, active);

  const edgeOffset = unit * (compact ? 0.008 : 0.01);
  const secondaryRadius = radius + edgeOffset;
  ctx.strokeStyle = colorWithAlpha(profile.electric, active ? 0.24 : 0.14);
  ctx.lineWidth = Math.max(compact ? 0.7 : 0.9, unit * 0.0018);
  ctx.shadowColor = profile.electric;
  ctx.shadowBlur = compact ? 1.2 : 1.8;
  ctx.beginPath();
  ctx.arc(centerX, centerY, secondaryRadius, 0, TWO_PI);
  ctx.stroke();

  const innerRadius = radius - edgeOffset;
  ctx.strokeStyle = colorWithAlpha(profile.hot, active ? 0.78 : 0.68);
  ctx.lineWidth = Math.max(1.1, unit * 0.0032);
  ctx.shadowColor = profile.hot;
  ctx.shadowBlur = compact ? 2 : 3;
  ctx.beginPath();
  ctx.arc(centerX, centerY, innerRadius, 0, TWO_PI);
  ctx.stroke();

  if (pulse > 0) {
    const pulseRadius = radius + (1 - pulse) * unit * (compact ? 0.11 : 0.13);
    ctx.strokeStyle = colorWithAlpha(profile.hot, pulse * 0.72);
    ctx.lineWidth = Math.max(0.85, compact ? 0.9 : 1.1);
    ctx.setLineDash([unit * 0.012, unit * 0.025]);
    ctx.shadowColor = profile.electric;
    ctx.shadowBlur = compact ? 2 : 4;
    ctx.beginPath();
    ctx.arc(centerX, centerY, pulseRadius, 0, TWO_PI);
    ctx.stroke();
  }
  ctx.restore();
}

function drawSignalArcs(
  ctx: CanvasRenderingContext2D,
  centerX: number,
  centerY: number,
  unit: number,
  time: number,
  profile: CoreProfile,
  currentState: CoreVisualState,
  compact: boolean,
  audioLevel: number,
  reduceMotion: boolean,
): void {
  const moving = !reduceMotion && !["idle", "approval_wait", "offline", "degraded", "error"].includes(currentState);
  const drift = moving ? time * 0.00012 * (1 + profile.motion) : 0;
  const audio = ["listening", "transcribing", "speaking"].includes(currentState) ? audioLevel : 0;
  const radius = compact ? 0.36 : 0.4;
  const width = compact ? 0.0024 + audio * 0.001 : 0.0018 + audio * 0.0008;
  const arcs: ArcSpec[] = compact
    ? [
        { radius, start: -2.4, end: -1.78, alpha: 0.58, width, blur: 1 },
        { radius, start: 0.72, end: 1.42, alpha: 0.42, width: 0.0022 },
        { radius, start: 2.32, end: 2.72, alpha: 0.3, width: 0.0018 },
      ]
    : [
        { radius, start: -2.48, end: -1.78, alpha: 0.6, width, blur: 1 },
        { radius, start: -1.12, end: -0.7, alpha: 0.34, width: 0.0016 },
        { radius, start: 0.78, end: 1.54, alpha: 0.42, width: 0.0019 },
        { radius, start: 2.14, end: 2.56, alpha: 0.3, width: 0.0015 },
      ];

  arcs.forEach((arc, index) => drawArc(
    ctx,
    centerX,
    centerY,
    unit,
    { ...arc, color: index === 0 ? profile.electric : profile.accent },
    profile,
    drift * (index % 2 ? -0.35 : 0.22),
  ));

  if (moving) {
    drawArc(ctx, centerX, centerY, unit, {
      radius: radius + 0.014,
      start: -0.78,
      end: -0.62,
      alpha: 0.56,
      width: 0.0018,
      color: profile.hot,
      blur: 1,
    }, profile, drift * 0.8);
  }
}

function drawCalibration(
  ctx: CanvasRenderingContext2D,
  centerX: number,
  centerY: number,
  unit: number,
  profile: CoreProfile,
  compact: boolean,
): void {
  const count = compact ? 8 : 16;
  const outer = unit * (compact ? 0.425 : 0.46);
  const baseInner = unit * (compact ? 0.395 : 0.425);

  for (let i = 0; i < count; i += 1) {
    if (!compact && i % 9 === 4) continue;
    const angle = -Math.PI / 2 + (i / count) * TWO_PI;
    const major = i % (compact ? 4 : 5) === 0;
    const inner = baseInner - (major ? unit * 0.018 : 0);
    const color = major ? profile.hot : profile.electric;
    drawLine(
      ctx,
      centerX + Math.cos(angle) * inner,
      centerY + Math.sin(angle) * inner,
      centerX + Math.cos(angle) * outer,
      centerY + Math.sin(angle) * outer,
      color,
      major ? 0.42 : compact ? 0.22 : 0.28,
      major ? 1 : 0.7,
    );
  }

  const asymmetry = compact ? 1 : 2;
  for (let i = 0; i < asymmetry; i += 1) {
    const angle = -1.12 + i * 0.13;
    const inner = unit * 0.32;
    const outerPoint = unit * (0.37 + (i % 2) * 0.015);
    drawLine(
      ctx,
      centerX + Math.cos(angle) * inner,
      centerY + Math.sin(angle) * inner,
      centerX + Math.cos(angle) * outerPoint,
      centerY + Math.sin(angle) * outerPoint,
      profile.electric,
      compact ? 0.24 : 0.3,
      0.8,
    );
  }
}

function drawSignalNodes(
  ctx: CanvasRenderingContext2D,
  centerX: number,
  centerY: number,
  unit: number,
  profile: CoreProfile,
  compact: boolean,
): void {
  const nodes = compact
    ? [
        { angle: -0.92, radius: 0.42, size: 1.7, alpha: 0.72 },
        { angle: 2.36, radius: 0.435, size: 1.2, alpha: 0.42 },
      ]
    : [
        { angle: -0.92, radius: 0.42, size: 2, alpha: 0.76 },
        { angle: -2.38, radius: 0.445, size: 1.35, alpha: 0.46 },
        { angle: 0.52, radius: 0.445, size: 1.4, alpha: 0.44 },
        { angle: 2.36, radius: 0.435, size: 1.3, alpha: 0.4 },
      ];

  nodes.forEach((node, index) => {
    const x = centerX + Math.cos(node.angle) * unit * node.radius;
    const y = centerY + Math.sin(node.angle) * unit * node.radius;
    ctx.save();
    ctx.fillStyle = colorWithAlpha(index === 0 ? profile.hot : profile.electric, node.alpha * profile.energy);
    ctx.shadowColor = profile.electric;
    ctx.shadowBlur = Math.min(unit * 0.008, index === 0 ? 3 : 1.5);
    ctx.beginPath();
    ctx.arc(x, y, node.size, 0, TWO_PI);
    ctx.fill();
    ctx.restore();
  });
}

function drawPulse(
  ctx: CanvasRenderingContext2D,
  centerX: number,
  centerY: number,
  unit: number,
  pulse: number,
  profile: CoreProfile,
  compact: boolean,
): void {
  if (pulse <= 0) return;
  const radius = unit * ((compact ? 0.3 : 0.34) + (1 - pulse) * (compact ? 0.1 : 0.12));
  ctx.save();
  ctx.strokeStyle = colorWithAlpha(profile.hot, pulse * 0.46);
  ctx.lineWidth = Math.max(0.8, unit * (compact ? 0.0017 : 0.002));
  ctx.setLineDash([unit * 0.014, unit * 0.024]);
  ctx.shadowColor = profile.electric;
  ctx.shadowBlur = Math.min(unit * 0.01, 4);
  ctx.beginPath();
  ctx.arc(centerX, centerY, radius, 0, TWO_PI);
  ctx.stroke();
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
  reduceMotion: boolean,
  compact: boolean,
): void {
  const profile = PROFILES[currentState] || PROFILES.idle;
  const centerX = width / 2;
  const centerY = height / 2;
  const unit = Math.min(width, height);
  const elapsed = reduceMotion ? 0 : time;
  const statePulse = currentState === "success"
    ? Math.max(0, Math.min(1, (time - pulseStartedAt) / 800))
    : 0;
  const clickPulse = Math.max(0, Math.min(1, (time - clickPulseStartedAt) / 500));
  const pulse = Math.max(statePulse > 0 ? 1 - statePulse : 0, clickPulse > 0 ? 1 - clickPulse : 0);

  ctx.clearRect(0, 0, width, height);
  drawCoreField(ctx, centerX, centerY, unit, elapsed, profile, compact, reduceMotion);
  drawConcentricStructure(
    ctx,
    centerX,
    centerY,
    unit,
    elapsed,
    profile,
    compact,
    reduceMotion,
    pulse,
    audioLevel,
    currentState,
  );
  drawSignalArcs(ctx, centerX, centerY, unit, elapsed, profile, currentState, compact, audioLevel, reduceMotion);
  drawCalibration(ctx, centerX, centerY, unit, profile, compact);
  drawSignalNodes(ctx, centerX, centerY, unit, profile, compact);
  drawPulse(ctx, centerX, centerY, unit, pulse, profile, compact);
}

interface CharlieRingProps {
  compact?: boolean;
}

export function CharlieRing({ compact = false }: CharlieRingProps): ReactElement {
  const coreState = useCharlieStore((state) => state.coreState);
  const visualPhase = useCharlieStore((state) => state.visualRuntime.phase);
  const connected = useCharlieStore((state) => state.connected);

  const canvasRef = useRef<HTMLCanvasElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const initialState = normalizeState(visualPhase === "offline" && connected ? coreState : visualPhase, connected);
  const stateRef = useRef<CoreVisualState>(initialState);
  const lastStateRef = useRef<CoreVisualState>(initialState);
  const audioLevelRef = useRef(0);
  const pulseStartedAtRef = useRef(0);
  const clickPulseStartedAtRef = useRef(0);
  const reduceMotionRef = useRef(false);
  const compactRef = useRef(compact);
  compactRef.current = compact;

  const state = normalizeState(visualPhase === "offline" && connected ? coreState : visualPhase, connected);

  useEffect(() => {
    const previousState = lastStateRef.current;
    stateRef.current = state;
    lastStateRef.current = state;
    if (state !== previousState && state === "success") {
      pulseStartedAtRef.current = performance.now();
    }
  }, [state]);

  // Audio levels update refs so microphone energy never drives React re-renders.
  useEffect(() => {
    if (containerRef.current) {
      containerRef.current.setAttribute("data-audio-level", String(useCharlieStore.getState().audioLevel));
    }
    const unsub = useCharlieStore.subscribe((storeState) => {
      audioLevelRef.current = storeState.audioLevel;
      if (containerRef.current) {
        containerRef.current.setAttribute("data-audio-level", String(storeState.audioLevel));
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
      drawFrame(
        context,
        width,
        height,
        performance.now(),
        stateRef.current,
        audioLevelRef.current,
        pulseStartedAtRef.current,
        clickPulseStartedAtRef.current,
        reduceMotionRef.current,
        compactRef.current,
      );
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
          reduceMotionRef.current,
          compactRef.current,
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
      data-core-scale={compact ? "docked" : "centered"}
      data-geometry="concentric-segments-calibration"
      data-state={state}
      role="img"
      aria-label={`Charlie ${label}`}
      onClick={handleClick}
    >
      <canvas ref={canvasRef} className="hud-core-canvas" aria-hidden="true" />
      <OuterHudSystem compact={compact} />
    </div>
  );
}
