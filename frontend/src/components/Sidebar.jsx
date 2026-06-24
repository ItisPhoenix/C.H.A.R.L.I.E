import { MessageSquare, RefreshCw, Plus, Clock, Compass } from 'lucide-react';

function groupSessionsByDate(sessions) {
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const yesterday = new Date(today);
  yesterday.setDate(yesterday.getDate() - 1);
  const lastWeek = new Date(today);
  lastWeek.setDate(lastWeek.getDate() - 7);

  const groups = { Today: [], Yesterday: [], 'Last 7 Days': [], Older: [] };

  sessions.forEach((session) => {
    const d = new Date(session.created_at);
    if (d >= today) groups.Today.push(session);
    else if (d >= yesterday) groups.Yesterday.push(session);
    else if (d >= lastWeek) groups['Last 7 Days'].push(session);
    else groups.Older.push(session);
  });

  return Object.entries(groups).filter(([, s]) => s.length > 0);
}

function formatTime(ts) {
  const d = new Date(ts);
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

export function Sidebar({ sessions = [], loading = false, onRefresh, currentSessionId, onSelectSession, onNewChat }) {
  const grouped = groupSessionsByDate(sessions);

  return (
    <div className="flex flex-col h-full bg-zinc-950 border-r border-zinc-900/80 select-none">
      {/* New Chat Button (Double-Bezel Nesting) */}
      <div className="p-4 border-b border-zinc-900/50">
        <div className="p-1 bg-zinc-900/30 border border-zinc-900 rounded-[1.25rem] shadow-inner">
          <button
            onClick={onNewChat}
            className="w-full flex items-center justify-center gap-2.5 px-4 py-2.5 bg-zinc-900 hover:bg-zinc-800 border border-zinc-800/60 hover:border-zinc-700/85 rounded-xl text-xs font-semibold text-white transition-all duration-500 ease-[cubic-bezier(0.32,0.72,0,1)] active:scale-[0.98] group shadow-md"
          >
            <Plus size={14} className="text-indigo-400 group-hover:rotate-90 transition-transform duration-500" />
            <span>New Session</span>
          </button>
        </div>
      </div>

      {/* Header */}
      <div className="flex items-center justify-between px-5 py-3 border-b border-zinc-900/30 bg-zinc-950">
        <span className="text-[10px] font-bold text-zinc-500 uppercase tracking-[0.2em]">Conversations</span>
        <button
          onClick={onRefresh}
          disabled={loading}
          className="text-zinc-600 hover:text-zinc-400 p-1 rounded hover:bg-zinc-900/50 transition-colors duration-350"
        >
          <RefreshCw size={11} className={loading ? 'animate-spin' : ''} />
        </button>
      </div>

      {/* Session List */}
      <div className="flex-1 overflow-y-auto px-2 py-3 space-y-4 scrollbar-thin">
        {grouped.length === 0 && (
          <div className="flex flex-col items-center justify-center mt-12 px-4 text-center">
            <Compass size={24} className="text-zinc-700 mb-2.5 stroke-[1.5]" />
            <p className="text-[11px] text-zinc-500 font-medium">No active threads</p>
            <p className="text-[10px] text-zinc-600 mt-1 max-w-[150px]">Start a new conversation with Charlie.</p>
          </div>
        )}
        {grouped.map(([label, sessionsInGroup]) => (
          <div key={label} className="space-y-1">
            <div className="px-3 py-1 text-[9px] font-bold text-zinc-600 uppercase tracking-widest">
              {label}
            </div>
            <div className="space-y-0.5">
              {sessionsInGroup.map((session) => {
                const isActive = currentSessionId === session.id;
                return (
                  <div key={session.id} className="p-0.5">
                    <button
                      onClick={() => onSelectSession(session.id)}
                      className={`w-full text-left px-3 py-2.5 rounded-xl text-xs transition-all duration-500 ease-[cubic-bezier(0.32,0.72,0,1)] active:scale-[0.99] group border ${
                        isActive
                          ? 'bg-zinc-900 border-zinc-800/80 shadow-md text-white'
                          : 'bg-transparent border-transparent text-zinc-400 hover:text-zinc-200 hover:bg-zinc-900/30'
                      }`}
                    >
                      <div className="flex items-center gap-2.5">
                        <MessageSquare 
                          size={13} 
                          className={`shrink-0 transition-colors ${
                            isActive ? 'text-indigo-400' : 'text-zinc-600 group-hover:text-zinc-400'
                          }`} 
                        />
                        <div className="min-w-0 flex-1">
                          <p className={`truncate font-medium ${isActive ? 'text-white' : 'text-zinc-300'}`}>
                            {session.title || 'Untitled Session'}
                          </p>
                          <p className="text-[9px] text-zinc-500 mt-0.5 flex items-center gap-1.5 font-mono">
                            <Clock size={8} />
                            {formatTime(session.created_at)}
                          </p>
                        </div>
                      </div>
                    </button>
                  </div>
                );
              })}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
