import { useState, useEffect } from 'react';
import { Wrench, ChevronDown, ChevronRight, Terminal } from 'lucide-react';

export function ToolLog({ onMessage, currentSessionId }) {
  const [entries, setEntries] = useState([]);
  const [expanded, setExpanded] = useState(true);

  useEffect(() => {
    setEntries([]);
  }, [currentSessionId]);

  useEffect(() => {
    const unsubscribe = onMessage((event) => {
      if (event.type === 'tool_call') {
        setEntries((prev) => [
          ...prev,
          {
            id: crypto.randomUUID?.() ?? String(Date.now()),
            type: 'call',
            name: event.payload.name,
            args: event.payload.args,
            timestamp: new Date().toLocaleTimeString(),
          },
        ]);
        return;
      }

      if (event.type === 'tool_result') {
        setEntries((prev) => {
          const lastCall = [...prev].reverse().find((e) => e.type === 'call');
          if (!lastCall) return prev;
          return prev.map((e) =>
            e.id === lastCall.id
              ? { ...e, result: event.payload.text }
              : e,
          );
        });
      }
    });
    return unsubscribe;
  }, [onMessage]);

  return (
    <div className="border-t border-[var(--border-soft)] bg-[var(--surface-elevated)]">
      <button
        type="button"
        onClick={() => setExpanded((prev) => !prev)}
        className="flex items-center gap-2 w-full px-5 py-2.5 text-xs font-medium text-[var(--text-muted)] hover:text-[var(--text-primary)] transition-colors duration-500 ease-[cubic-bezier(0.32,0.72,0,1)]"
      >
        <Terminal size={14} className="text-[var(--accent)]" />
        <span>Tool Calls</span>
        <span className="text-[10px] text-[var(--text-muted)]">
          ({entries.length})
        </span>
        <span className="ml-auto">
          {expanded ? (
            <ChevronDown size={14} className="text-[var(--text-muted)]" />
          ) : (
            <ChevronRight size={14} className="text-[var(--text-muted)]" />
          )}
        </span>
      </button>

      {expanded && (
        <div className="max-h-56 overflow-y-auto px-5 pb-4 space-y-2">
          {entries.length === 0 && (
            <p className="text-xs text-[var(--text-muted)] italic">
              No tool calls yet
            </p>
          )}
          {entries.map((entry) => (
            <ToolEntry key={entry.id} entry={entry} />
          ))}
        </div>
      )}
    </div>
  );
}

function ToolEntry({ entry }) {
  const [showDetails, setShowDetails] = useState(false);

  return (
    <div className="rounded-xl border border-[var(--border-soft)] bg-[var(--surface-muted)]">
      <button
        type="button"
        onClick={() => setShowDetails((prev) => !prev)}
        className="flex items-center gap-2 w-full px-3 py-2 text-left"
      >
        <span className="text-[11px] font-mono text-[var(--accent)]">
          {entry.name}
        </span>
        <span className="text-[10px] text-[var(--text-muted)] font-mono">
          {entry.timestamp}
        </span>
        {entry.result && (
          <span className="ml-auto text-[10px] font-medium text-emerald-400">
            done
          </span>
        )}
        <span className="ml-1 text-[var(--text-muted)]">
          {showDetails ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        </span>
      </button>
      {showDetails && (
        <div className="px-3 pb-2 space-y-1 border-t border-[var(--border-soft)]">
          {entry.args && (
            <div className="text-[11px]">
              <span className="text-[var(--text-muted)]">Args: </span>
              <span className="font-mono text-[var(--text-primary)] break-all">
                {entry.args}
              </span>
            </div>
          )}
          {entry.result && (
            <div className="text-[11px]">
              <span className="text-[var(--text-muted)]">Result: </span>
              <span className="font-mono text-[var(--text-primary)] break-all">
                {entry.result.slice(0, 200)}
                {entry.result.length > 200 && '...'}
              </span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
