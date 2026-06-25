import { useState, useEffect, useCallback } from 'react';
import { Sparkles, Brain, Wrench, ChevronDown, ChevronRight, Loader } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';

const CARD_ICONS = {
  thinking_update: Brain,
  tool_call: Wrench,
  tool_result: Wrench,
};

const CARD_COLORS = {
  thinking_update: 'text-amber-400 border-amber-500/20',
  tool_call: 'text-violet-400 border-[var(--accent)]/20',
  tool_result: 'text-emerald-400 border-emerald-500/20',
};

const COLLAPSE_THRESHOLD = 5;

function ActivityCard({ entry }) {
  const Icon = CARD_ICONS[entry.type] || Sparkles;
  const colorClass = CARD_COLORS[entry.type] || 'text-[var(--text-muted)] border-white/[0.06]';

  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: 8, scale: 0.97 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, y: -8, scale: 0.97 }}
      transition={{ duration: 0.2 }}
      className={`rounded-xl border bg-[var(--surface-muted)] px-3 py-2.5 ${colorClass.split(' ')[1]}`}
    >
      <div className="flex items-start gap-2">
        {entry.type === 'tool_call' && entry.pending ? (
          <Loader size={13} className={`shrink-0 mt-0.5 animate-spin ${colorClass.split(' ')[0]}`} />
        ) : (
          <Icon size={13} className={`shrink-0 mt-0.5 ${colorClass.split(' ')[0]}`} />
        )}
        <div className="min-w-0 flex-1">
          <p className="text-xs leading-relaxed text-[var(--text-primary)] break-words">{entry.text}</p>
          {entry.detail && (
            <p className="text-[10px] text-[var(--text-secondary)] mt-1 truncate">{entry.detail}</p>
          )}
        </div>
        {entry.step != null && (
          <span className="text-[9px] text-[var(--text-secondary)] shrink-0">#{entry.step}</span>
        )}
      </div>
    </motion.div>
  );
}

function CollapsedEntries({ count }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <button
      type="button"
      onClick={() => setExpanded(!expanded)}
      className="flex items-center gap-1.5 px-3 py-1 text-[10px] text-[var(--text-secondary)] hover:text-[var(--text-muted)] transition-colors"
    >
      {expanded ? <ChevronDown size={10} /> : <ChevronRight size={10} />}
      <span>{expanded ? 'Hide' : `Show ${count} previous steps`}</span>
    </button>
  );
}

export function SmartPanel({ visible = true, onClose, onMessage, currentSessionId }) {
  const [entries, setEntries] = useState([]);
  const [stepCounter, setStepCounter] = useState(0);

  // Clear on session change
  useEffect(() => {
    setEntries([]);
    setStepCounter(0);
  }, [currentSessionId]);

  // Subscribe to WS events
  useEffect(() => {
    if (!onMessage) return;

    const unsubscribe = onMessage((event) => {
      if (event.type === 'thinking_update') {
        setStepCounter((prev) => prev + 1);
        setEntries((prev) => [
          { type: 'thinking_update', text: event.payload?.text || 'Thinking...', step: stepCounter + 1, id: Date.now() },
          ...prev,
        ]);
      } else if (event.type === 'tool_call') {
        const name = event.payload?.name || 'tool';
        const args = event.payload?.args;
        const detail = args ? JSON.stringify(args).slice(0, 80) : '';
        setEntries((prev) => [
          { type: 'tool_call', text: `Running ${name}`, detail, pending: true, id: Date.now() },
          ...prev,
        ]);
      } else if (event.type === 'tool_result') {
        const name = event.payload?.name || 'tool';
        const text = event.payload?.text || '';
        const summary = text.length > 100 ? text.slice(0, 100) + '...' : text;
        setEntries((prev) => {
          // Mark most recent pending tool_call as resolved
          const updated = prev.map((e) =>
            e.type === 'tool_call' && e.pending ? { ...e, pending: false } : e
          );
          return [
            { type: 'tool_result', text: `${name} completed`, detail: summary, id: Date.now() },
            ...updated,
          ];
        });
      }
    });
    return unsubscribe;
  }, [onMessage, stepCounter, currentSessionId]);

  if (!visible) return null;

  // Split: recent (visible) vs collapsed (old)
  const recent = entries.slice(0, COLLAPSE_THRESHOLD);
  const collapsed = entries.slice(COLLAPSE_THRESHOLD);

  return (
    <motion.div
      initial={{ x: 60, opacity: 0 }}
      animate={{ x: 0, opacity: 1 }}
      exit={{ x: 60, opacity: 0 }}
      transition={{ duration: 0.25, ease: 'easeOut' }}
      className="w-80 shrink-0 h-full glass-strong border-l border-[var(--glass-border)] flex flex-col z-30 select-none"
    >
      {/* Header */}
      <div className="flex items-center justify-between px-5 py-4 border-b border-white/[0.06]">
        <h2 className="text-sm font-semibold text-[var(--text-primary)]">Activity</h2>
        <button
          type="button"
          onClick={onClose}
          className="p-1 rounded-lg hover:bg-white/[0.06] text-[var(--text-muted)] hover:text-[var(--text-primary)] transition-colors text-xs"
          title="Close panel"
        >
          ✕
        </button>
      </div>

      {/* Content */}
      {entries.length === 0 ? (
        <div className="flex-1 flex flex-col items-center justify-center px-6 text-center">
          <div className="w-12 h-12 rounded-2xl bg-gradient-to-br from-[var(--accent)]/20 to-violet-500/10 border border-[var(--accent)]/10 flex items-center justify-center mb-3">
            <Sparkles size={20} className="text-[var(--accent)]" />
          </div>
          <p className="text-sm text-[var(--text-muted)]">Activity will appear here</p>
          <p className="text-xs text-[var(--text-secondary)] mt-1">Tool calls, thinking steps, and more</p>
        </div>
      ) : (
        <div className="flex-1 overflow-y-auto px-3 py-3 space-y-2">
          {/* Collapsed old entries */}
          {collapsed.length > 0 && (
            <CollapsedEntries count={collapsed.length} />
          )}

          {/* Recent entries */}
          <AnimatePresence mode="popLayout">
            {recent.map((entry) => (
              <ActivityCard key={entry.id} entry={entry} />
            ))}
          </AnimatePresence>
        </div>
      )}

      {/* Footer */}
      <div className="px-5 py-3 border-t border-white/[0.06] text-center">
        <span className="text-[10px] text-[var(--text-secondary)] uppercase tracking-widest">
          {entries.length > 0 ? `${entries.length} steps` : 'Phase 2'}
        </span>
      </div>
    </motion.div>
  );
}
