import { MessageSquare, RefreshCw, Plus, Clock } from 'lucide-react';

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
    <div className="flex flex-col h-full bg-gray-900 border-r border-gray-700">
      {/* New Chat Button */}
      <div className="p-3 border-b border-gray-700">
        <button
          onClick={onNewChat}
          className="w-full flex items-center justify-center gap-2 px-3 py-2 bg-gray-800 hover:bg-gray-700 rounded-lg text-sm text-gray-300 transition-colors"
        >
          <Plus size={14} />
          New Chat
        </button>
      </div>

      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2 border-b border-gray-700">
        <h2 className="text-xs font-semibold text-gray-500 uppercase">Sessions</h2>
        <button
          onClick={onRefresh}
          disabled={loading}
          className="text-gray-600 hover:text-gray-400 transition-colors"
        >
          <RefreshCw size={12} className={loading ? 'animate-spin' : ''} />
        </button>
      </div>

      {/* Session List */}
      <div className="flex-1 overflow-y-auto">
        {grouped.length === 0 && (
          <p className="text-xs text-gray-600 italic text-center mt-4">
            No sessions yet
          </p>
        )}
        {grouped.map(([label, sessionsInGroup]) => (
          <div key={label}>
            <div className="px-4 py-2 text-[10px] font-semibold text-gray-600 uppercase tracking-wider">
              {label}
            </div>
            {sessionsInGroup.map((session) => (
              <button
                key={session.id}
                onClick={() => onSelectSession(session.id)}
                className={`w-full text-left px-4 py-2 text-xs transition-colors ${
                  currentSessionId === session.id
                    ? 'bg-indigo-600/20 text-white border-r-2 border-indigo-500'
                    : 'text-gray-400 hover:bg-gray-800 hover:text-gray-200'
                }`}
              >
                <div className="flex items-center gap-2">
                  <MessageSquare size={12} className="shrink-0 text-gray-600" />
                  <div className="min-w-0 flex-1">
                    <p className="truncate font-medium">{session.title}</p>
                    <p className="text-[10px] text-gray-600 flex items-center gap-1">
                      <Clock size={8} />
                      {formatTime(session.created_at)}
                    </p>
                  </div>
                </div>
              </button>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}
