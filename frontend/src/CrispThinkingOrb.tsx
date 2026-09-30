import { useEffect, useRef } from "react";
import { MODE_FRAMES, paintFrame, resolvePreset, type OrbState } from "thinking-orbs/engine";

const DRAW_SIZE = 256;

export function CrispThinkingOrb({ state, speed = 0.9 }: { state: OrbState; speed?: number }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    canvas.width = Math.round(DRAW_SIZE * dpr);
    canvas.height = Math.round(DRAW_SIZE * dpr);
    canvas.style.width = `${DRAW_SIZE}px`;
    canvas.style.height = `${DRAW_SIZE}px`;
    const context = canvas.getContext("2d");
    if (!context) return;

    const { mode, speed: presetSpeed, opts } = resolvePreset(state, 64);
    const frame = MODE_FRAMES[mode];
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const draw = (seconds: number) => {
      context.setTransform(dpr, 0, 0, dpr, 0, 0);
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

  return <canvas ref={canvasRef} className="crisp-thinking-orb" role="img" aria-label={`Charlie ${state}`} />;
}
