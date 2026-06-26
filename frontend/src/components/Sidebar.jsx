import { Plus, RefreshCw, Trash2 } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
function parseDate(ts) {
  if (!ts) return new Date(NaN);
  if (typeof ts === 'string' && !ts.includes('T')) {
    // SQLite UTC space format "YYYY-MM-DD HH:MM:SS.SSS" -> ISO "YYYY-MM-DDTHH:MM:SS.SSSZ"
    const normalized = ts.replace(' ', 'T') + 'Z';
    return new Date(normalized);
  }
  return new Date(ts);
}


function groupSessionsByDate(sessions) {
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const yesterday = new Date(today);
  yesterday.setDate(yesterday.getDate() - 1);
  const lastWeek = new Date(today);
  lastWeek.setDate(lastWeek.getDate() - 7);

  const groups = { Today: [], Yesterday: [], 'Last 7 Days': [], Older: [] };

  sessions.forEach((session) => {
    const d = parseDate(session.updated_at || session.created_at);
    if (d >= today) groups.Today.push(session);
    else if (d >= yesterday) groups.Yesterday.push(session);
    else if (d >= lastWeek) groups['Last 7 Days'].push(session);
    else groups.Older.push(session);
  });

  return Object.entries(groups).filter(([, s]) => s.length > 0);
}

function formatRelativeTime(ts) {
  if (!ts) return '';
  const now = Date.now();
  const then = parseDate(ts).getTime();
  if (isNaN(then)) return '';
  const diffSec = Math.floor((now - then) / 1000);
  if (diffSec < 60) return 'just now';
  const diffMin = Math.floor(diffSec / 60);
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHr = Math.floor(diffMin / 60);
  if (diffHr < 24) return `${diffHr}h ago`;
  const diffDay = Math.floor(diffHr / 24);
  if (diffDay < 7) return `${diffDay}d ago`;
  return parseDate(ts).toLocaleDateString([], { month: 'short', day: 'numeric' });
}

export function Sidebar({
  sessions = [],
  loading = false,
  onRefresh,
  currentSessionId,
  onSelectSession,
  onNewChat,
  filterMode = 'launch',
  onFilterModeChange,
  collapsed = false,
  onDeleteSession,
}) {
  const grouped = groupSessionsByDate(sessions);

  if (collapsed) {
    return (
      <div className="flex flex-col items-center h-full glass-strong border-r border-white/[0.06] py-4 gap-4 w-16 shrink-0 select-none z-30">
        <button
          type="button"
          onClick={onNewChat}
          className="w-10 h-10 flex items-center justify-center rounded-xl border border-[var(--accent)]/30 text-[var(--accent)] hover:bg-[var(--accent)]/10 transition-all"
          title="New Chat"
        >
          <Plus size={18} />
        </button>
        <button
          type="button"
          onClick={onRefresh}
          className="w-10 h-10 flex items-center justify-center rounded-xl border border-white/[0.06] text-[var(--text-muted)] hover:text-[var(--text-primary)] transition-all"
          title="Refresh"
        >
          <RefreshCw size={16} />
        </button>
      </div>
    );
  }

  return (
    <motion.div
      initial={{ x: -20, opacity: 0 }}
      animate={{ x: 0, opacity: 1 }}
      transition={{ duration: 0.4, ease: [0.25, 0.1, 0.25, 1] }}
      className="flex flex-col h-full w-72 shrink-0 glass-strong border-r border-white/[0.06] select-none z-30"
    >
      {/* New Chat Button */}
      <div className="p-4 border-b border-white/[0.06]">
        <motion.button
          type="button"
          onClick={onNewChat}
          whileHover={{ scale: 1.02, boxShadow: '0 0 20px rgba(167,139,250,0.1)' }}
          whileTap={{ scale: 0.97 }}
          className="w-full flex items-center justify-center gap-2 rounded-2xl py-3 px-4 border border-white/[0.06] text-[var(--text-primary)] hover:border-[var(--accent)]/40 transition-all duration-300"
        >
          <Plus size={16} className="text-[var(--accent)]" />
          <span className="text-sm font-medium">New Chat</span>
        </motion.button>
      </div>

      {/* Header */}
      <div className="flex items-center justify-between px-5 py-3 border-b border-white/[0.06]">
        <div>
          <h2 className="text-sm font-semibold text-[var(--text-primary)]">Conversations</h2>
          <p className="text-[11px] text-[var(--text-muted)]">{sessions.length} sessions</p>
        </div>
        <button
          type="button"
          onClick={onRefresh}
          className="p-1.5 rounded-lg hover:bg-white/[0.06] text-[var(--text-muted)] hover:text-[var(--text-primary)] transition-colors"
          title="Refresh"
        >
          <RefreshCw size={14} />
        </button>
      </div>

      {/* Filter Toggle */}
      <div className="px-4 py-2 border-b border-white/[0.06]">
        <div className="flex gap-1 p-0.5 rounded-xl bg-white/[0.03] border border-white/[0.06]">
          {['launch', 'all'].map((mode) => (
            <button
              key={mode}
              type="button"
              onClick={() => onFilterModeChange?.(mode)}
              className={`flex-1 py-1.5 px-3 rounded-lg text-[11px] font-medium uppercase tracking-wider transition-all duration-300 ${
                filterMode === mode
                  ? 'text-[var(--accent)] border border-[var(--accent)]/30 shadow-[0_0_12px_rgba(167,139,250,0.1)]'
                  : 'text-[var(--text-muted)] border border-transparent hover:text-[var(--text-primary)]'
              }`}
            >
              {mode === 'launch' ? 'This Launch' : 'All'}
            </button>
          ))}
        </div>
      </div>

      {/* Session List */}
      <div className="flex-1 overflow-y-auto px-2 py-3 space-y-4" style={{ maskImage: 'linear-gradient(to bottom, black 85%, transparent 100%)', WebkitMaskImage: 'linear-gradient(to bottom, black 85%, transparent 100%)' }}>
        {loading && sessions.length === 0 && (
          <div className="flex justify-center py-8">
            <div className="w-5 h-5 border-2 border-[var(--accent)]/30 border-t-[var(--accent)] rounded-full animate-spin" />
          </div>
        )}

        {!loading && sessions.length === 0 && (
          <motion.div
            initial={{ opacity: 0, scale: 0.9 }}
            animate={{ opacity: 0.6, scale: 1 }}
            transition={{ duration: 0.5 }}
            className="flex flex-col items-center justify-center py-12 px-4 text-center"
          >
            <div className="w-10 h-10 rounded-2xl bg-white/[0.06] border border-white/[0.08] flex items-center justify-center mb-3">
              <span className="text-lg">💬</span>
            </div>
            <p className="text-sm text-[var(--text-muted)]">No conversations yet</p>
          </motion.div>
        )}

        <AnimatePresence mode="popLayout">
          {grouped.map(([label, items]) => (
            <div key={label}>
              {/* Group header */}
              <div className="flex items-center gap-2 px-3 mb-2">
                <div className="w-[3px] h-3 rounded-full bg-[var(--accent)]/50" />
                <span className="text-[11px] uppercase tracking-wider text-[var(--text-muted)] font-medium">{label}</span>
              </div>

              {/* Session items */}
              {items.map((session) => {
                const isActive = session.id === currentSessionId;
                const isCurrentLaunch = session.launch_id != null && session.launch_id !== '';
                return (
                  <motion.div
                    key={session.id}
                    layout
                    initial={{ opacity: 0, y: 8 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, y: -8 }}
                    transition={{ duration: 0.2 }}
                    onClick={() => onSelectSession(session.id)}
                    className={`w-full flex items-center gap-1 rounded-xl px-3 py-2.5 mb-1 transition-colors duration-200 group cursor-pointer ${
                      isActive
                        ? 'bg-white/[0.06] ring-1 ring-[var(--accent)]/50'
                        : 'hover:bg-white/[0.04]'
                    }`}
                  >
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        {isCurrentLaunch && (
                          <div className="w-1.5 h-1.5 rounded-full bg-[var(--accent)] shrink-0" />
                        )}
                        <span className={`text-sm font-medium truncate ${isActive ? 'text-[var(--text-primary)]' : 'text-[var(--text-primary)] opacity-80 group-hover:opacity-100'}`}>
                          {session.title || 'New Chat'}
                        </span>
                      </div>
                      <span className="text-xs text-[var(--text-muted)] ml-3.5 block mt-0.5">
                        {formatRelativeTime(session.updated_at || session.created_at)}
                      </span>
                    </div>
                    {onDeleteSession && (
                      <button
                        type="button"
                        onClick={(e) => { e.stopPropagation(); onDeleteSession(session.id); }}
                        className="shrink-0 p-1.5 rounded-lg opacity-0 group-hover:opacity-100 hover:bg-red-500/15 text-[var(--text-muted)] hover:text-red-400 transition-all duration-200"
                        title="Delete chat"
                      >
                        <Trash2 size={13} />
                      </button>
                    )}
                  </motion.div>
                );
              })}
            </div>
          ))}
        </AnimatePresence>
      </div>
    </motion.div>
  );
}
