import { useState, useEffect, useRef, useCallback } from 'react';
import { Mic, Brain, Volume2, Wifi, MicOff } from 'lucide-react';
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
    if (status === 'idle') return;
    const t = Date.now() * 0.005;
    const next = Array.from({ length: BAR_COUNT }, (_, i) => {
      if (status === 'listening') return 4 + Math.sin(i * 0.8 + t) * 8 + Math.random() * 4;
      if (status === 'thinking') return 4 + Math.sin(i * 0.5 + t * 0.6) * 5;
      if (status === 'speaking') return 4 + Math.sin(i * 0.6 + t * 0.8) * 10 + Math.cos(i * 1.2) * 3;
      return 3;
    });
    setHeights(next);
    frameRef.current = requestAnimationFrame(tick);
  }, [status]);

  useEffect(() => {
    if (status === 'idle') {
      if (frameRef.current) cancelAnimationFrame(frameRef.current);
      setHeights(Array(BAR_COUNT).fill(3));
      return;
    }
    frameRef.current = requestAnimationFrame(tick);
    return () => {
      if (frameRef.current) cancelAnimationFrame(frameRef.current);
    };
  }, [tick, status]);

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

export function VoiceDock({ status = 'idle', wakeWordPulse = false }) {
  const config = STATE_CONFIG[status] || STATE_CONFIG.idle;
  const Icon = config.icon;
  const isListening = status === 'listening';

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
      <motion.div
        key={status}
        initial={{ scale: 0.9, opacity: 0 }}
        animate={{ scale: 1, opacity: 1 }}
        transition={{ duration: 0.3 }}
        className={`flex items-center gap-2 px-4 py-1.5 rounded-full border ${config.pillBorder} ${config.pillGlow || ''} backdrop-blur-lg`}
      >
        <motion.div
          animate={
            isListening
              ? { scale: [1, 1.3, 1], opacity: [0.6, 1, 0.6] }
              : status === 'thinking'
                ? { rotate: [0, 360] }
                : status === 'speaking'
                  ? { scale: [1, 1.15, 1] }
                  : { scale: 1, opacity: 0.5 }
          }
          transition={
            isListening || status === 'speaking'
              ? { duration: 1.2, repeat: Infinity, ease: 'easeInOut' }
              : status === 'thinking'
                ? { duration: 2, repeat: Infinity, ease: 'linear' }
                : { duration: 0.3 }
          }
        >
          <Icon size={12} className={`${config.color} shrink-0`} />
        </motion.div>
        <span className={`text-xs font-medium ${config.color} transition-colors duration-300`}>
          {config.label}
        </span>
      </motion.div>

      {/* Right: mic status indicator */}
      <div className="w-40 flex justify-end relative">
        {wakeWordPulse && (
          <motion.div
            initial={{ scale: 0.8, opacity: 0.8 }}
            animate={{ scale: 2.2, opacity: 0 }}
            transition={{ duration: 1.5, ease: 'easeOut' }}
            className="absolute inset-0 rounded-xl border-2 border-[#a78bfa] pointer-events-none z-10"
          />
        )}
        <motion.div
          animate={
            wakeWordPulse
              ? { boxShadow: ['0 0 0 0 rgba(167,139,250,0)', '0 0 0 8px rgba(167,139,250,0.25)', '0 0 0 0 rgba(167,139,250,0)'] }
              : isListening
                ? { boxShadow: ['0 0 0 0 rgba(52,211,153,0)', '0 0 0 6px rgba(52,211,153,0.15)', '0 0 0 0 rgba(52,211,153,0)'] }
                : { boxShadow: '0 0 0 0 rgba(0,0,0,0)' }
          }
          transition={
            wakeWordPulse
              ? { duration: 1.5, ease: 'easeOut' }
              : isListening
                ? { duration: 1.5, repeat: Infinity, ease: 'easeInOut' }
                : { duration: 0.3 }
          }
          className={`flex items-center gap-1.5 px-3 py-1.5 rounded-xl border transition-all duration-300 ${
            wakeWordPulse
              ? 'border-[#a78bfa]/40 text-[#a78bfa]'
              : isListening
                ? 'border-emerald-500/30 text-emerald-400'
                : 'border-white/[0.06] text-[var(--text-muted)]'
          }`}
          title={wakeWordPulse ? 'Wake word detected — listening...' : isListening ? 'Hardware mic is active — voice is being captured' : 'Mic is idle'}
        >
          {isListening || wakeWordPulse ? <Mic size={13} /> : <MicOff size={13} />}
          <span className="text-[10px] font-medium uppercase tracking-wider">
            {wakeWordPulse ? 'Listening...' : isListening ? 'Live' : 'Mic'}
          </span>
        </motion.div>
      </div>
    </motion.div>
  );
}
