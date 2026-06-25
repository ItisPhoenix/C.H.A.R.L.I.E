import { Mic, Brain, Volume2, Wifi, WifiOff } from 'lucide-react';

const STATUS_CONFIG = {
  idle: { label: 'Idle', color: 'bg-zinc-400', glow: '', icon: Wifi, text: 'text-zinc-400' },
  listening: { label: 'Listening...', color: 'bg-emerald-400', glow: 'shadow-[0_0_12px_rgba(52,211,153,0.3)]', icon: Mic, text: 'text-emerald-400' },
  thinking: { label: 'Thinking...', color: 'bg-amber-400', glow: 'shadow-[0_0_12px_rgba(251,191,36,0.3)]', icon: Brain, text: 'text-amber-400' },
  speaking: { label: 'Speaking...', color: 'bg-violet-400', glow: 'shadow-[0_0_12px_rgba(167,139,250,0.3)]', icon: Volume2, text: 'text-violet-400' },
};

export function StatusBar({ status, wsConnected }) {
  const config = STATUS_CONFIG[status] || STATUS_CONFIG.idle;
  const Icon = config.icon;

  return (
    <header className="relative flex items-center justify-between px-6 py-4 backdrop-blur-md border-b border-white/[0.06] sticky top-0 z-50 after:content-[''] after:absolute after:bottom-0 after:left-0 after:right-0 after:h-[1px] after:bg-gradient-to-r after:from-transparent after:via-white/[0.04] after:to-transparent">
      {/* Brand */}
      <div className="flex items-center gap-3">
        <div className="relative flex items-center justify-center w-8 h-8 rounded-xl bg-white/[0.06] border border-white/[0.08] shadow-inner">
          <span className="text-xs font-bold text-white tracking-widest">C</span>
          <div className="absolute -bottom-0.5 -right-0.5 w-2 h-2 rounded-full bg-[var(--accent)] animate-pulse" />
        </div>
        <div>
          <h1 className="text-base font-semibold text-[var(--text-primary)] tracking-tight">C.H.A.R.L.I.E.</h1>
          <p className="text-[11px] text-[var(--text-muted)] uppercase tracking-widest font-medium">Truth Engine</p>
        </div>
      </div>

      {/* Center status badge */}
      <div className="flex items-center gap-2.5 px-4 py-1.5 rounded-full border border-white/[0.06] shadow-[0_4px_12px_rgba(0,0,0,0.2)] backdrop-blur-lg">
        <div className={`w-2 h-2 rounded-full ${config.color} ${config.glow} animate-pulse`} />
        <Icon size={12} className={`${config.text} shrink-0`} />
        <span className="text-xs font-medium text-[var(--text-primary)] opacity-80">{config.label}</span>
      </div>

      {/* Connection badge */}
      <div className={`flex items-center gap-1.5 px-3 py-1 rounded-full text-[10px] font-semibold uppercase tracking-wider transition-all duration-500 ${
        wsConnected 
          ? 'border border-emerald-500/20 text-emerald-400' 
          : 'border border-red-500/20 text-red-400'
      }`}>
        {wsConnected ? (
          <>
            <Wifi size={10} className="animate-pulse" />
            <span>Online</span>
          </>
        ) : (
          <>
            <WifiOff size={10} />
            <span>Offline</span>
          </>
        )}
      </div>
    </header>
  );
}
