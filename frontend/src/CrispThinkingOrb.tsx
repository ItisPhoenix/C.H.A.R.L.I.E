import { useEffect, useRef } from "react";
import { MODE_FRAMES, paintFrame, resolvePreset, type OrbState } from "thinking-orbs/engine";

const DRAW_SIZE = 256;

export function CrispThinkingOrb({ state, speed = 0.9 }: { state: OrbState; speed?: number }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    canvas.style.width = `${DRAW_SIZE}px`;
    canvas.style.height = `${DRAW_SIZE}px`;
    const context = canvas.getContext("2d");
    if (!context) return;

    // 64 selects density/speed tuning; frame() computes new geometry at 256.
    // No 64px bitmap is created or enlarged.
    const { mode, speed: presetSpeed, opts } = resolvePreset(state, 64);
    const frame = MODE_FRAMES[mode];
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const draw = (seconds: number) => {
      const pixels = Math.round(DRAW_SIZE * (window.devicePixelRatio || 1));
      if (canvas.width !== pixels || canvas.height !== pixels) {
        canvas.width = pixels;
        canvas.height = pixels;
      }
      const scale = pixels / DRAW_SIZE;
      context.setTransform(scale, 0, 0, scale, 0, 0);
      context.filter = "none";
      context.shadowBlur = 0;
      context.clearRect(0, 0, DRAW_SIZE, DRAW_SIZE);
      paintFrame(context, frame(DRAW_SIZE, seconds, opts), true);
    };

    if (reduced) {
      draw(0.6);
      return;
    }

    let animationFrame = 0;
    let visible = true;
    const loop = () => {
      draw((performance.now() / 1000) * presetSpeed * speed);
      animationFrame = requestAnimationFrame(loop);
    };
    const observer = typeof IntersectionObserver === "undefined"
      ? null
      : new IntersectionObserver(([entry]) => {
          visible = entry.isIntersecting;
          if (visible && !animationFrame) animationFrame = requestAnimationFrame(loop);
          if (!visible && animationFrame) {
            cancelAnimationFrame(animationFrame);
            animationFrame = 0;
          }
        });
    observer?.observe(canvas);
    const onVisibility = () => {
      if (document.visibilityState === "hidden" && animationFrame) {
        cancelAnimationFrame(animationFrame);
        animationFrame = 0;
      } else if (document.visibilityState === "visible" && visible && !animationFrame) {
        animationFrame = requestAnimationFrame(loop);
      }
    };
    document.addEventListener("visibilitychange", onVisibility);
    draw(performance.now() / 1000 * presetSpeed * speed);
    animationFrame = requestAnimationFrame(loop);
    return () => {
      if (animationFrame) cancelAnimationFrame(animationFrame);
      observer?.disconnect();
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [speed, state]);

  return <canvas ref={canvasRef} width={DRAW_SIZE} height={DRAW_SIZE} className="crisp-thinking-orb" role="img" aria-label={`Charlie ${state}`} />;
}
