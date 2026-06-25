import { useState, useEffect, useRef, useCallback } from 'react';
import { Mic, Brain, Volume2, Wifi } from 'lucide-react';
import { motion } from 'framer-motion';

const STATE_CONFIG = {
  idle: { label: 'Idle', color: 'text-zinc-400', pillBorder: 'border-white/[0.06]', icon: Wifi, barColor: 'bg-zinc-500' },
  listening: { label: 'Listening...', color: 'text-emerald-400', pillBorder: 'border-emerald-500/20', pillGlow: 'shadow-[0_0_16px_rgba(52,211,153,0.15)]', icon: Mic, barColor: 'bg-emerald-400' },
  thinking: { label: 'Thinking...', color: 'text-amber-400', pillBorder: 'border-amber-500/20', pillGlow: 'shadow-[0_0_16px_rgba(251,191,36,0.15)]', icon: Brain, barColor: 'bg-amber-400' },
  speaking: { label: 'Speaking...', color: 'text-[var(--accent)]', pillBorder: 'border-[var(--accent)]/20', pillGlow: 'shadow-[0_0_16px_rgba(167,139,250,0.15)]', icon: Volume2, barColor: 'bg-[var(--accent)]' },
};

const BAR_COUNT = 16;

function useWaveform(status) {
  const [heights, setHeights] = useState(() => Array(BAR_COUNT).fill(3));
  const frameRef = useRef(null);

  const tick = useCallback(() => {
    const t = Date.now() * 0.005;
    const next = Array.from({ length: BAR_COUNT }, (_, i) => {
      if (status === 'idle') return 3;
      if (status === 'listening') return 4 + Math.sin(i * 0.8 + t) * 8 + Math.random() * 4;
      if (status === 'thinking') return 4 + Math.sin(i * 0.5 + t * 0.6) * 5;
      if (status === 'speaking') return 4 + Math.sin(i * 0.6 + t * 0.8) * 10 + Math.cos(i * 1.2) * 3;
      return 3;
    });
    setHeights(next);
    frameRef.current = requestAnimationFrame(tick);
  }, [status]);

  useEffect(() => {
    frameRef.current = requestAnimationFrame(tick);
    return () => {
      if (frameRef.current) cancelAnimationFrame(frameRef.current);
    };
  }, [tick]);

  return heights;
}

function WaveformBars({ status }) {
  const heights = useWaveform(status);
  const config = STATE_CONFIG[status] || STATE_CONFIG.idle;

  return (
    <div className="flex items-center gap-[3px] h-6">
      {heights.map((h, i) => (
        <div
          key={i}
          className={`w-[3px] rounded-full transition-all duration-75 ${config.barColor}`}
          style={{ height: h, opacity: status === 'idle' ? 0.3 : 0.5 + (h / 18) * 0.5 }}
        />
      ))}
    </div>
  );
}

export function VoiceDock({ status = 'idle' }) {
  const config = STATE_CONFIG[status] || STATE_CONFIG.idle;
  const Icon = config.icon;

  return (
    <motion.div
      initial={{ y: '100%' }}
      animate={{ y: 0 }}
      transition={{ type: 'spring', stiffness: 300, damping: 30 }}
      className="w-full h-14 border-t border-[var(--glass-border)] backdrop-blur-2xl bg-[var(--glass-bg)] z-40 flex items-center justify-between px-6 shrink-0"
    >
      {/* Waveform */}
      <div className="w-40">
        <WaveformBars status={status} />
      </div>

      {/* Status pill */}
      <div className={`flex items-center gap-2 px-4 py-1.5 rounded-full border ${config.pillBorder} ${config.pillGlow || ''} backdrop-blur-lg transition-all duration-300`}>
        <Icon size={12} className={`${config.color} shrink-0`} />
        <span className={`text-xs font-medium ${config.color} transition-colors duration-300`}>
          {config.label}
        </span>
      </div>

      {/* Right: mic button stub */}
      <div className="w-40 flex justify-end">
        <button
          type="button"
          className="p-2 rounded-xl border border-white/[0.06] text-[var(--text-muted)] hover:text-[var(--text-primary)] hover:bg-white/[0.06] transition-all"
          title="Microphone (stub)"
        >
          <Mic size={14} />
        </button>
      </div>
    </motion.div>
  );
}
