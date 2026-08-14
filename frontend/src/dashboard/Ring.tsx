import { useEffect, useRef, type PointerEvent, type ReactElement } from "react";
import { useCharlieStore } from "../store/charlie";
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

interface Point3 {
  x: number;
  y: number;
  z: number;
  radius: number;
  size: number;
  alpha: number;
  twinkle: number;
  phase: number;
  cool: boolean;
}

interface Orbit {
  radiusX: number;
  radiusY: number;
  depth: number;
  tilt: number;
  yaw: number;
  roll: number;
  speed: number;
  phase: number;
  direction: number;
  nodes: number;
}

interface OrbitSpark {
  angle: number;
  radius: number;
  yScale: number;
  size: number;
  alpha: number;
  phase: number;
  speed: number;
  cool: boolean;
}

interface ProjectedPoint {
  x: number;
  y: number;
  z: number;
  scale: number;
}

interface ProjectedParticle {
  x: number;
  y: number;
  z: number;
  scale: number;
}

interface CoreShellPoint {
  x: number;
  y: number;
  z: number;
  size: number;
  phase: number;
}

interface CoreProfile {
  energy: number;
  motion: number;
  accent: string;
  hot: string;
  deep: string;
  cool: string;
}

interface PointerPosition {
  x: number;
  y: number;
}

const PARTICLE_COUNT = 2800;
const TWO_PI = Math.PI * 2;
const GOLDEN_ANGLE = Math.PI * (3 - Math.sqrt(5));
const CORE_SHELL_POINTS: CoreShellPoint[] = Array.from({ length: 520 }, (_, index) => {
  const y = 1 - (index + 0.5) / 260;
  const radius = Math.sqrt(Math.max(0, 1 - y * y));
  const angle = index * GOLDEN_ANGLE;
  return {
    x: Math.cos(angle) * radius * (0.86 + (index % 7) * 0.02),
    y: y * (0.86 + (index % 7) * 0.02),
    z: Math.sin(angle) * radius * (0.86 + (index % 7) * 0.02),
    size: 0.45 + (index % 5) * 0.2,
    phase: (index % 19) * 0.33,
  };
});

const PROFILES: Record<CoreVisualState, CoreProfile> = {
  idle: { energy: 0.72, motion: 0.12, accent: "#ff9b2e", hot: "#fff1bd", deep: "#8e2d00", cool: "#7bdcff" },
  listening: { energy: 0.9, motion: 0.34, accent: "#ffae3d", hot: "#fff5d2", deep: "#a13c00", cool: "#73e6ff" },
  thinking: { energy: 0.88, motion: 0.58, accent: "#ffb23f", hot: "#fff6d0", deep: "#963000", cool: "#8acbff" },
  working: { energy: 0.94, motion: 0.52, accent: "#ff9b2e", hot: "#fff0b5", deep: "#8b2700", cool: "#73d9ff" },
  speaking: { energy: 0.84, motion: 0.3, accent: "#ffb84e", hot: "#fff5d0", deep: "#9d3900", cool: "#8deaff" },
  waiting: { energy: 0.62, motion: 0.12, accent: "#ee8b25", hot: "#ffe5a6", deep: "#762300", cool: "#6bbfdc" },
  attention: { energy: 0.95, motion: 0.42, accent: "#ffc65a", hot: "#fff8dc", deep: "#ad4b00", cool: "#8ce4ff" },
  completed: { energy: 1, motion: 0.62, accent: "#ffc45a", hot: "#fffbe7", deep: "#a33b00", cool: "#b0edff" },
  error: { energy: 0.8, motion: 0.22, accent: "#ff884c", hot: "#fff0df", deep: "#8f210d", cool: "#8ac9d9" },
  offline: { energy: 0.75, motion: 0.035, accent: "#ff9b2e", hot: "#fff0b5", deep: "#762800", cool: "#6baabd" },
};

const ORBITS: Orbit[] = [
  { radiusX: 0.27, radiusY: 0.125, depth: 0.035, tilt: 0.12, yaw: 0.18, roll: -0.28, speed: 0.06, phase: 0.2, direction: 1, nodes: 1 },
  { radiusX: 0.25, radiusY: 0.17, depth: 0.03, tilt: 0.86, yaw: -0.2, roll: 0.52, speed: 0.045, phase: 1.8, direction: -1, nodes: 1 },
  { radiusX: 0.3, radiusY: 0.105, depth: 0.028, tilt: -0.47, yaw: 0.72, roll: -0.1, speed: 0.034, phase: 3.4, direction: 1, nodes: 2 },
  { radiusX: 0.24, radiusY: 0.19, depth: 0.025, tilt: 1.18, yaw: -0.8, roll: 0.16, speed: 0.028, phase: 0.9, direction: -1, nodes: 1 },
  { radiusX: 0.29, radiusY: 0.145, depth: 0.022, tilt: -0.92, yaw: 0.28, roll: 0.78, speed: 0.022, phase: 2.7, direction: 1, nodes: 1 },
  { radiusX: 0.26, radiusY: 0.205, depth: 0.018, tilt: 0.35, yaw: 1.1, roll: -0.46, speed: 0.017, phase: 4.2, direction: -1, nodes: 1 },
  { radiusX: 0.32, radiusY: 0.135, depth: 0.015, tilt: -0.18, yaw: -1.35, roll: 0.34, speed: 0.012, phase: 5.1, direction: 1, nodes: 1 },
];

const ORBIT_SPARKS: OrbitSpark[] = (() => {
  const random = seededRandom(0x0a70b17);
  return Array.from({ length: 72 }, (_, index) => ({
    angle: random() * TWO_PI,
    radius: 0.145 + random() ** 0.7 * 0.17,
    yScale: 0.82 + random() * 0.26,
    size: 0.38 + random() * 0.62,
    alpha: 0.08 + random() * 0.25,
    phase: random() * TWO_PI,
    speed: (index % 2 === 0 ? 1 : -1) * (0.0015 + random() * 0.0025),
    cool: index === 23 || index === 61,
  }));
})();

const CONNECTIONS = Array.from({ length: 260 }, (_, index) => {
  const first = (index * 17) % PARTICLE_COUNT;
  return [first, (first + (index % 3 === 0 ? 17 : 29)) % PARTICLE_COUNT] as const;
});

function seededRandom(seed: number): () => number {
  let value = seed >>> 0;
  return () => {
    value = (value * 1664525 + 1013904223) >>> 0;
    return value / 4294967296;
  };
}

function createParticles(): Point3[] {
  const random = seededRandom(0xc4a71e);
  return Array.from({ length: PARTICLE_COUNT }, (_, index) => {
    const theta = index * GOLDEN_ANGLE + random() * 0.18;
    const y = 1 - (index / (PARTICLE_COUNT - 1)) * 2;
    const radiusOnSphere = Math.sqrt(Math.max(0, 1 - y * y));
    const band = index % 10;
    const radius = band < 4
      ? 0.12 + random() ** 1.55 * 0.62
      : band < 8
        ? 0.5 + random() * 0.28
        : 0.78 + random() * 0.22;
    return {
      x: Math.cos(theta) * radiusOnSphere * radius,
      y: y * radius,
      z: Math.sin(theta) * radiusOnSphere * radius,
      radius,
      size: 0.45 + random() * 1.45,
      alpha: 0.22 + random() * 0.72,
      twinkle: 0.35 + random() * 0.9,
      phase: random() * TWO_PI,
      cool: false,
    };
  });
}

function normalizeState(rawState: string, connected: boolean): CoreVisualState {
  if (!connected) return "offline";
  if (rawState === "executing") return "working";
  if (rawState in PROFILES) return rawState as CoreVisualState;
  return "idle";
}

function profileFor(state: CoreVisualState): CoreProfile {
  return PROFILES[state];
}

function rotatePoint(point: Point3, rx: number, ry: number, rz: number): Point3 {
  const sinX = Math.sin(rx);
  const cosX = Math.cos(rx);
  const sinY = Math.sin(ry);
  const cosY = Math.cos(ry);
  const sinZ = Math.sin(rz);
  const cosZ = Math.cos(rz);
  const y1 = point.y * cosX - point.z * sinX;
  const z1 = point.y * sinX + point.z * cosX;
  const x2 = point.x * cosY + z1 * sinY;
  const z2 = -point.x * sinY + z1 * cosY;
  return { ...point, x: x2 * cosZ - y1 * sinZ, y: x2 * sinZ + y1 * cosZ, z: z2 };
}

function project(point: Point3, centerX: number, centerY: number, scale: number, tilt: PointerPosition): ProjectedPoint {
  const perspective = 1 / (1.16 - point.z * 0.2);
  return {
    x: centerX + point.x * scale * perspective + tilt.x * scale * 0.015,
    y: centerY + point.y * scale * perspective + tilt.y * scale * 0.015,
    z: point.z,
    scale: perspective,
  };
}

function drawGlow(ctx: CanvasRenderingContext2D, x: number, y: number, radius: number, color: string, alpha: number): void {
  const gradient = ctx.createRadialGradient(x, y, 0, x, y, radius);
  gradient.addColorStop(0, colorWithAlpha(color, alpha));
  gradient.addColorStop(0.36, colorWithAlpha(color, alpha * 0.32));
  gradient.addColorStop(1, colorWithAlpha(color, 0));
  ctx.fillStyle = gradient;
  ctx.beginPath();
  ctx.arc(x, y, radius, 0, TWO_PI);
  ctx.fill();
}

function colorWithAlpha(color: string, alpha: number): string {
  const hex = color.replace("#", "");
  const value = Number.parseInt(hex, 16);
  return `rgba(${value >> 16}, ${(value >> 8) & 255}, ${value & 255}, ${alpha})`;
}

function drawArc(ctx: CanvasRenderingContext2D, centerX: number, centerY: number, radius: number, start: number, end: number, color: string, width: number, alpha: number): void {
  ctx.strokeStyle = colorWithAlpha(color, alpha);
  ctx.lineWidth = width;
  ctx.beginPath();
  ctx.arc(centerX, centerY, radius, start, end);
  ctx.stroke();
}

function drawInnerFeedbackSystem(ctx: CanvasRenderingContext2D, centerX: number, centerY: number, unit: number, time: number, profile: CoreProfile, pulse: number): void {
  const drift = time * 0.0024;
  ctx.save();
  ctx.globalCompositeOperation = "lighter";
  ctx.lineCap = "round";

  drawArc(ctx, centerX, centerY, unit * 0.292, 0.34 - drift * 1.1, 2.1 - drift * 1.1, profile.accent, Math.max(0.55, unit * 0.0014), 0.54);
  drawArc(ctx, centerX, centerY, unit * 0.292, 3.04 + drift, 4.38 + drift, profile.cool, Math.max(0.35, unit * 0.0008), 0.24);
  drawArc(ctx, centerX, centerY, unit * 0.268, -1.62 - drift * 1.3, -0.37 - drift * 1.3, profile.hot, Math.max(0.4, unit * 0.00095), 0.36);

  if (pulse > 0) {
    drawArc(ctx, centerX, centerY, unit * (0.13 + pulse * 0.15), -1.2 + pulse * TWO_PI, -0.12 + pulse * TWO_PI, profile.hot, 1.4, (1 - pulse) * 0.82);
  }
  ctx.restore();
}

function drawOrbitSparks(ctx: CanvasRenderingContext2D, centerX: number, centerY: number, unit: number, time: number, profile: CoreProfile): void {
  ctx.save();
  ctx.globalCompositeOperation = "lighter";
  for (const spark of ORBIT_SPARKS) {
    const angle = spark.angle + time * spark.speed;
    const x = centerX + Math.cos(angle) * spark.radius * unit;
    const y = centerY + Math.sin(angle) * spark.radius * spark.yScale * unit;
    const twinkle = 0.68 + Math.sin(time * 0.32 + spark.phase) * 0.32;
    const radius = Math.max(0.32, spark.size * unit / 520);
    ctx.fillStyle = colorWithAlpha(spark.cool ? profile.cool : profile.accent, spark.alpha * twinkle);
    ctx.beginPath();
    ctx.arc(x, y, radius, 0, TWO_PI);
    ctx.fill();
  }
  ctx.restore();
}

function drawOrbitSystem(ctx: CanvasRenderingContext2D, centerX: number, centerY: number, unit: number, time: number, profile: CoreProfile, motion: number): void {
  const orbitScale = unit * 1.28;
  ctx.save();
  ctx.globalCompositeOperation = "lighter";
  ctx.lineCap = "round";
  ORBITS.forEach((orbit, orbitIndex) => {
    const points: ProjectedPoint[] = [];
    const orbitTime = time * orbit.speed * (1 + motion * 0.8) * orbit.direction + orbit.phase;
    for (let step = 0; step <= 96; step += 1) {
      const angle = step / 96 * TWO_PI;
      const local: Point3 = {
        x: Math.cos(angle) * orbit.radiusX,
        y: Math.sin(angle) * orbit.radiusY,
        z: Math.sin(angle * 2 + orbit.phase) * orbit.depth,
        radius: 1,
        size: 1,
        alpha: 1,
        twinkle: 1,
        phase: 0,
        cool: orbitIndex === 2,
      };
      const rotated = rotatePoint(local, orbit.tilt, orbit.yaw + orbitTime * 0.05, orbit.roll);
      points.push(project(rotated, centerX, centerY, orbitScale, { x: 0, y: 0 }));
    }
    ctx.strokeStyle = colorWithAlpha(orbitIndex === 2 ? profile.cool : profile.accent, orbitIndex % 3 === 0 ? 0.62 : 0.4);
    ctx.lineWidth = orbitIndex % 3 === 0 ? 0.94 : 0.6;
    ctx.beginPath();
    points.forEach((point, index) => {
      if (index === 0) ctx.moveTo(point.x, point.y);
      else ctx.lineTo(point.x, point.y);
    });
    ctx.stroke();

    if (orbitIndex === 0 || orbitIndex === 2 || orbitIndex === 3 || orbitIndex === 5) {
      const highlightStart = [7, 0, 31, 52, 0, 69, 0][orbitIndex] ?? 0;
      const highlightEnd = Math.min(points.length, highlightStart + (orbitIndex === 2 ? 9 : 13));
      ctx.save();
      ctx.strokeStyle = colorWithAlpha(orbitIndex === 2 ? profile.cool : profile.hot, orbitIndex === 2 ? 0.52 : 0.68);
      ctx.lineWidth = orbitIndex === 3 ? 1.15 : 0.88;
      ctx.shadowColor = orbitIndex === 2 ? profile.cool : profile.accent;
      ctx.shadowBlur = unit * 0.005;
      ctx.beginPath();
      for (let index = highlightStart; index < highlightEnd; index += 1) {
        const point = points[index];
        if (index === highlightStart) ctx.moveTo(point.x, point.y);
        else ctx.lineTo(point.x, point.y);
      }
      ctx.stroke();
      ctx.restore();
    }

    for (let nodeIndex = 0; nodeIndex < orbit.nodes; nodeIndex += 1) {
      const nodeAngle = orbitTime + nodeIndex * TWO_PI / orbit.nodes;
      const local: Point3 = {
        x: Math.cos(nodeAngle) * orbit.radiusX,
        y: Math.sin(nodeAngle) * orbit.radiusY,
        z: Math.sin(nodeAngle * 2 + orbit.phase) * orbit.depth,
        radius: 1,
        size: 1,
        alpha: 1,
        twinkle: 1,
        phase: 0,
        cool: orbitIndex === 2,
      };
      const node = project(rotatePoint(local, orbit.tilt, orbit.yaw + orbitTime * 0.05, orbit.roll), centerX, centerY, orbitScale, { x: 0, y: 0 });
      const color = orbitIndex === 2 ? profile.cool : orbitIndex % 3 === 0 ? profile.hot : profile.accent;
      const nodeRadius = unit * ([0.0065, 0.005, 0.006, 0.0085, 0.0055, 0.0075, 0.0048][orbitIndex] ?? 0.0055) * node.scale;
      drawGlow(ctx, node.x, node.y, nodeRadius * 4.4, color, 0.18 + profile.energy * 0.12);
      ctx.fillStyle = color;
      ctx.beginPath();
      ctx.arc(node.x, node.y, nodeRadius, 0, TWO_PI);
      ctx.fill();
    }
  });
  ctx.restore();
}

function drawParticleSphere(ctx: CanvasRenderingContext2D, centerX: number, centerY: number, unit: number, time: number, profile: CoreProfile, state: CoreVisualState, audioLevel: number, pointer: PointerPosition, particles: Point3[], projectedParticles: ProjectedParticle[]): void {
  const activity = Math.max(profile.energy, audioLevel * 0.9);
  const rotationY = time * (0.014 + profile.motion * 0.012) + pointer.x * 0.028;
  const rotationX = Math.sin(time * 0.003) * 0.08 + pointer.y * 0.028;
  const rotationZ = Math.sin(time * 0.002) * 0.025;
  const sinX = Math.sin(rotationX);
  const cosX = Math.cos(rotationX);
  const sinY = Math.sin(rotationY);
  const cosY = Math.cos(rotationY);
  const sinZ = Math.sin(rotationZ);
  const cosZ = Math.cos(rotationZ);
  const particleScale = unit * 0.113;

  for (let index = 0; index < particles.length; index += 1) {
    const particle = particles[index];
    const y1 = particle.y * cosX - particle.z * sinX;
    const z1 = particle.y * sinX + particle.z * cosX;
    const x2 = particle.x * cosY + z1 * sinY;
    const z2 = -particle.x * sinY + z1 * cosY;
    const rotatedX = x2 * cosZ - y1 * sinZ;
    const rotatedY = x2 * sinZ + y1 * cosZ;
    const perspective = 1 / (1.16 - z2 * 0.2);
    const projected = projectedParticles[index];
    projected.x = centerX + rotatedX * particleScale * perspective + pointer.x * particleScale * 0.015;
    projected.y = centerY + rotatedY * particleScale * perspective + pointer.y * particleScale * 0.015;
    projected.z = z2;
    projected.scale = perspective;
  }

  ctx.save();
  ctx.globalCompositeOperation = "lighter";
  for (let index = 0; index < projectedParticles.length; index += 1) {
    const entry = projectedParticles[index];
    const particle = particles[index];
    const point = entry;
    const twinkle = 0.65 + Math.sin(time * particle.twinkle + particle.phase) * 0.35;
    const depthAlpha = 0.16 + Math.pow((point.z + 1) / 2, 0.72) * 0.78;
    const shellBoost = particle.radius > 0.82 ? 1.72 : particle.radius > 0.76 ? 1.28 : particle.radius < 0.5 ? 0.78 : 0.95;
    const alpha = Math.min(1, particle.alpha * twinkle * depthAlpha * activity * shellBoost);
    const radius = Math.max(0.35, particle.size * point.scale * (unit / 420));
    ctx.fillStyle = colorWithAlpha(particle.cool ? profile.cool : profile.accent, alpha);
    ctx.beginPath();
    ctx.arc(point.x, point.y, radius, 0, TWO_PI);
    ctx.fill();
  }

  ctx.strokeStyle = colorWithAlpha(profile.accent, state === "thinking" || state === "working" ? 0.2 : 0.13);
  ctx.lineWidth = 0.55;
  for (const [firstIndex, secondIndex] of CONNECTIONS) {
    const first = projectedParticles[firstIndex];
    const second = projectedParticles[secondIndex];
    if (Math.abs(first.z - second.z) > 0.9) continue;
    ctx.beginPath();
    ctx.moveTo(first.x, first.y);
    ctx.lineTo(second.x, second.y);
    ctx.stroke();
  }
  ctx.restore();
}

function drawCoreShell(ctx: CanvasRenderingContext2D, centerX: number, centerY: number, unit: number, time: number, profile: CoreProfile, pointer: PointerPosition): void {
  const shellRadius = unit * 0.113;
  const rotation = time * 0.055 + pointer.x * 0.02;
  const tilt = 0.32 + pointer.y * 0.025;
  const sinRotation = Math.sin(rotation);
  const cosRotation = Math.cos(rotation);
  const sinTilt = Math.sin(tilt);
  const cosTilt = Math.cos(tilt);

  ctx.save();
  ctx.globalCompositeOperation = "lighter";
  for (const particle of CORE_SHELL_POINTS) {
    const x1 = particle.x * cosRotation - particle.z * sinRotation;
    const z1 = particle.x * sinRotation + particle.z * cosRotation;
    const y2 = particle.y * cosTilt - z1 * sinTilt;
    const z2 = particle.y * sinTilt + z1 * cosTilt;
    const perspective = 1 / (1.18 - z2 * 0.24);
    const x = centerX + x1 * shellRadius * perspective;
    const y = centerY + y2 * shellRadius * perspective;
    const alpha = (0.34 + (z2 + 1) * 0.28) * (0.72 + Math.sin(time * 0.5 + particle.phase) * 0.18);
    ctx.fillStyle = colorWithAlpha(z2 > 0.2 ? profile.hot : profile.accent, alpha);
    ctx.beginPath();
    ctx.arc(x, y, Math.max(0.35, particle.size * perspective * (unit / 420)), 0, TWO_PI);
    ctx.fill();
  }

  ctx.save();
  ctx.setLineDash([Math.max(0.8, unit * 0.0018), Math.max(1.6, unit * 0.0042)]);
  ctx.strokeStyle = colorWithAlpha(profile.hot, 0.72);
  ctx.lineWidth = Math.max(0.55, unit * 0.00125);
  ctx.beginPath();
  ctx.arc(centerX, centerY, shellRadius * 0.91, 0, TWO_PI);
  ctx.stroke();
  ctx.restore();

  ctx.strokeStyle = colorWithAlpha(profile.accent, 0.38);
  ctx.lineWidth = 0.7;
  for (let index = -2; index <= 2; index += 1) {
    const latitude = index / 3;
    const width = shellRadius * Math.sqrt(Math.max(0.12, 1 - latitude * latitude));
    ctx.beginPath();
    ctx.ellipse(centerX, centerY + latitude * shellRadius * 0.72, width, shellRadius * (0.16 + Math.abs(latitude) * 0.05), rotation * 0.35, 0, TWO_PI);
    ctx.stroke();
  }
  for (let index = 0; index < 5; index += 1) {
    ctx.beginPath();
    ctx.ellipse(centerX, centerY, shellRadius, shellRadius * (0.22 + index * 0.1), rotation + index * 0.66, 0, TWO_PI);
    ctx.strokeStyle = colorWithAlpha(index === 2 ? profile.hot : profile.accent, index === 2 ? 0.46 : 0.27);
    ctx.stroke();
  }
  ctx.restore();
}

function drawNucleus(ctx: CanvasRenderingContext2D, centerX: number, centerY: number, unit: number, time: number, profile: CoreProfile, pulse: number, audioLevel: number): void {
  const breath = 1 + Math.sin(time * 0.35) * 0.035 + audioLevel * 0.035;
  const radius = unit * 0.0215 * breath;
  drawGlow(ctx, centerX, centerY, unit * 0.065 * breath, profile.accent, 0.1 + profile.energy * 0.06);
  drawGlow(ctx, centerX, centerY, radius * 1.3, profile.hot, 0.12 + profile.energy * 0.16);
  const gradient = ctx.createRadialGradient(centerX, centerY, 0, centerX, centerY, radius);
  gradient.addColorStop(0, "#ffffff");
  gradient.addColorStop(0.16, profile.hot);
  gradient.addColorStop(0.52, profile.accent);
  gradient.addColorStop(1, colorWithAlpha(profile.deep, 0));
  ctx.fillStyle = gradient;
  ctx.beginPath();
  ctx.arc(centerX, centerY, radius, 0, TWO_PI);
  ctx.fill();

  ctx.save();
  ctx.translate(centerX, centerY);
  ctx.rotate(time * 0.08);
  ctx.strokeStyle = colorWithAlpha(profile.hot, 0.4);
  ctx.lineWidth = 0.65;
  for (let index = 0; index < 18; index += 1) {
    const angle = index / 18 * TWO_PI;
    const length = radius * (1.35 + (index % 3) * 0.4);
    ctx.beginPath();
    ctx.moveTo(Math.cos(angle) * radius * 0.5, Math.sin(angle) * radius * 0.5);
    ctx.lineTo(Math.cos(angle) * length, Math.sin(angle) * length);
    ctx.stroke();
  }
  ctx.restore();

  if (pulse > 0) {
    ctx.strokeStyle = colorWithAlpha(profile.hot, (1 - pulse) * 0.9);
    ctx.lineWidth = 1.4;
    ctx.beginPath();
    ctx.arc(centerX, centerY, unit * (0.09 + pulse * 0.2), 0, TWO_PI);
    ctx.stroke();
  }
}

function drawFrame(ctx: CanvasRenderingContext2D, width: number, height: number, time: number, currentState: CoreVisualState, audioLevel: number, pointer: PointerPosition, pulseStartedAt: number, clickPulseStartedAt: number, reduceMotion: boolean, particles: Point3[], projectedParticles: ProjectedParticle[]): void {
  const profile = profileFor(currentState);
  const centerX = width / 2;
  const centerY = height / 2;
  const unit = Math.min(width, height);
  const elapsed = reduceMotion ? 0 : time * 0.001;
  const statePulse = currentState === "completed" ? Math.max(0, Math.min(1, (time - pulseStartedAt) / 900)) : 0;
  const clickPulse = Math.max(0, Math.min(1, (time - clickPulseStartedAt) / 560));
  const pulse = Math.max(statePulse > 0 ? 1 - statePulse : 0, clickPulse > 0 ? 1 - clickPulse : 0);
  const jitter = currentState === "error" ? Math.sin(elapsed * 0.8) * unit * 0.0014 : 0;

  ctx.clearRect(0, 0, width, height);
  ctx.save();
  ctx.translate(jitter, -jitter * 0.4);
  drawInnerFeedbackSystem(ctx, centerX, centerY, unit, elapsed, profile, pulse);
  drawOrbitSparks(ctx, centerX, centerY, unit, elapsed, profile);
  drawOrbitSystem(ctx, centerX, centerY, unit, elapsed, profile, profile.motion);
  drawGlow(ctx, centerX, centerY, unit * 0.138, profile.deep, 0.06 + profile.energy * 0.03);
  drawParticleSphere(ctx, centerX, centerY, unit, elapsed, profile, currentState, audioLevel, pointer, particles, projectedParticles);
  drawCoreShell(ctx, centerX, centerY, unit, elapsed, profile, pointer);
  drawNucleus(ctx, centerX, centerY, unit, elapsed, profile, pulse, audioLevel);
  ctx.restore();
}

export function Ring(): ReactElement {
  const coreState = useCharlieStore((state) => state.coreState);
  const connected = useCharlieStore((state) => state.connected);
  const audioLevel = useCharlieStore((state) => state.audioLevel);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const stateRef = useRef<CoreVisualState>(normalizeState(coreState, connected));
  const lastStateRef = useRef<CoreVisualState>(stateRef.current);
  const audioLevelRef = useRef(audioLevel);
  const pointerRef = useRef<PointerPosition>({ x: 0, y: 0 });
  const pulseStartedAtRef = useRef(0);
  const clickPulseStartedAtRef = useRef(0);
  const reduceMotionRef = useRef(false);

  const state = normalizeState(coreState, connected);
  audioLevelRef.current = audioLevel;

  useEffect(() => {
    const previousState = lastStateRef.current;
    stateRef.current = state;
    lastStateRef.current = state;
    if (state !== previousState && state === "completed") pulseStartedAtRef.current = performance.now();
  }, [state]);

  useEffect(() => {
    const canvas = canvasRef.current;
    const container = containerRef.current;
    if (!canvas || !container) return;
    const context = canvas.getContext("2d", { alpha: true });
    if (!context) return;

    const particles = createParticles();
    const projectedParticles = particles.map(() => ({ x: 0, y: 0, z: 0, scale: 1 }));
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
    const onReducedMotionChange = (event: MediaQueryListEvent) => { reduceMotionRef.current = event.matches; };
    mediaQuery.addEventListener("change", onReducedMotionChange);

    const render = (timestamp: number) => {
      if (stopped) return;
      if (!document.hidden) {
        drawFrame(context, width, height, timestamp, stateRef.current, audioLevelRef.current, pointerRef.current, pulseStartedAtRef.current, clickPulseStartedAtRef.current, reduceMotionRef.current, particles, projectedParticles);
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

  const handlePointerMove = (event: PointerEvent<HTMLDivElement>) => {
    const bounds = event.currentTarget.getBoundingClientRect();
    pointerRef.current = {
      x: (event.clientX - bounds.left) / bounds.width * 2 - 1,
      y: (event.clientY - bounds.top) / bounds.height * 2 - 1,
    };
  };

  const handlePointerLeave = () => { pointerRef.current = { x: 0, y: 0 }; };
  const handleClick = () => { clickPulseStartedAtRef.current = performance.now(); };
  const label = state === "offline" ? "Offline" : state;

  return (
    <div
      ref={containerRef}
      className="hud-ring"
      data-state={state}
      data-audio-level={audioLevel}
      role="img"
      aria-label={`Charlie ${label}`}
      onPointerMove={handlePointerMove}
      onPointerLeave={handlePointerLeave}
      onClick={handleClick}
    >
      <canvas ref={canvasRef} className="hud-core-canvas" aria-hidden="true" />
      <OuterHudSystem />
    </div>
  );
}
