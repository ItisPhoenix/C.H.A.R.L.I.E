import { Mic, Brain, Volume2, Wifi, WifiOff } from 'lucide-react';

const STATUS_CONFIG = {
  idle: { label: 'Idle', color: 'bg-zinc-500 shadow-zinc-500/20', icon: Wifi, text: 'text-zinc-400' },
  listening: { label: 'Listening...', color: 'bg-emerald-500 shadow-emerald-500/40', icon: Mic, text: 'text-emerald-400' },
  thinking: { label: 'Thinking...', color: 'bg-amber-500 shadow-amber-500/40', icon: Brain, text: 'text-amber-400' },
  speaking: { label: 'Speaking...', color: 'bg-indigo-500 shadow-indigo-500/40', icon: Volume2, text: 'text-indigo-400' },
};

export function StatusBar({ status, wsConnected }) {
  const config = STATUS_CONFIG[status] || STATUS_CONFIG.idle;
  const Icon = config.icon;

  return (
    <header className="flex items-center justify-between px-6 py-4 bg-zinc-950/80 backdrop-blur-md border-b border-zinc-900 sticky top-0 z-50">
      {/* Brand */}
      <div className="flex items-center gap-3">
        <div className="relative flex items-center justify-center w-8 h-8 rounded-lg bg-zinc-900 border border-zinc-800 shadow-inner">
          <span className="text-xs font-bold text-white tracking-widest">C</span>
          <div className="absolute -bottom-0.5 -right-0.5 w-2 h-2 rounded-full bg-indigo-500 animate-pulse" />
        </div>
        <div>
          <h1 className="text-sm font-semibold text-white tracking-tight">C.H.A.R.L.I.E.</h1>
          <p className="text-[10px] text-zinc-500 uppercase tracking-wider font-medium">Truth Engine v0.13</p>
        </div>
      </div>

      {/* Center status badge (floating glass) */}
      <div className="flex items-center gap-2.5 px-4 py-1.5 bg-zinc-900/60 border border-zinc-800/80 rounded-full shadow-[0_4px_12px_rgba(0,0,0,0.2)]">
        <div className={`w-2 h-2 rounded-full ${config.color} shadow-[0_0_8px_var(--tw-shadow-color)] animate-pulse`} />
        <Icon size={12} className={`${config.text} shrink-0`} />
        <span className="text-xs font-medium text-zinc-300">{config.label}</span>
      </div>

      {/* Connection State */}
      <div className="flex items-center gap-2">
        <div className={`flex items-center gap-1.5 px-3 py-1 rounded-full text-[10px] font-semibold uppercase tracking-wider transition-all duration-500 ${
          wsConnected 
            ? 'bg-emerald-950/30 border border-emerald-800/30 text-emerald-400' 
            : 'bg-red-950/30 border border-red-800/30 text-red-400'
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
      </div>
    </header>
  );
}
