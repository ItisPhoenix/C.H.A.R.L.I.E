'use client';

import { useEffect, useRef, useCallback } from 'react';

interface OrbProps {
  status: number; // 0=idle, 1=listening, 2=thinking, 3=speaking
  onDrag?: () => void;
}

export function Orb({ status, onDrag }: OrbProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const animRef = useRef<number>(0);

  const draw = useCallback((ctx: CanvasRenderingContext2D, time: number) => {
    const w = ctx.canvas.width;
    const h = ctx.canvas.height;
    const cx = w / 2;
    const cy = h / 2;
    const baseRadius = 40;

    ctx.clearRect(0, 0, w, h);

    // Colors per status
    const colors: Record<number, [string, string]> = {
      0: ['#444', '#222'],       // idle
      1: ['#00ccff', '#0066ff'], // listening
      2: ['#ffaa00', '#ff4400'], // thinking
      3: ['#00ff88', '#008844'], // speaking
    };
    const [c1, c2] = colors[status] || colors[0];

    // Pulsing radius
    const pulse = status > 0 ? Math.sin(time * 0.003) * 5 : 0;
    const r = baseRadius + pulse;

    // Gradient
    const grad = ctx.createRadialGradient(cx - 10, cy - 10, 0, cx, cy, r);
    grad.addColorStop(0, c1);
    grad.addColorStop(1, c2);
    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.fillStyle = grad;
    ctx.fill();

    // Glow
    ctx.shadowColor = c1;
    ctx.shadowBlur = status > 0 ? 30 : 10;

    // Ring for listening
    if (status === 1) {
      ctx.beginPath();
      ctx.arc(cx, cy, r + 8, 0, Math.PI * 2);
      ctx.strokeStyle = c1;
      ctx.lineWidth = 2;
      ctx.globalAlpha = 0.3 + Math.sin(time * 0.004) * 0.2;
      ctx.stroke();
    }
  }, [status]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    let running = true;
    const loop = (time: number) => {
      if (!running) return;
      draw(ctx, time);
      animRef.current = requestAnimationFrame(loop);
    };
    animRef.current = requestAnimationFrame(loop);
    return () => { running = false; cancelAnimationFrame(animRef.current); };
  }, [draw]);

  return (
    <div
      data-tauri-drag-region
      style={{
        width: '100%',
        height: '100%',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        cursor: 'grab',
      }}
      onClick={onDrag}
    >
      <canvas ref={canvasRef} width={200} height={200} />
    </div>
  );
}
