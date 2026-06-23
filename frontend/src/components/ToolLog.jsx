import { useState, useEffect } from 'react';
import { Wrench, ChevronDown, ChevronRight } from 'lucide-react';

export function ToolLog({ onMessage }) {
  const [entries, setEntries] = useState([]);
  const [expanded, setExpanded] = useState(true);

  useEffect(() => {
    const unsubscribe = onMessage((event) => {
      if (event.type === 'tool_call') {
        setEntries((prev) => [
          ...prev,
          {
            id: Date.now(),
            type: 'call',
            name: event.payload.name,
            args: event.payload.args,
            timestamp: new Date().toLocaleTimeString(),
          },
        ]);
      } else if (event.type === 'tool_result') {
        setEntries((prev) => {
          const lastCall = [...prev].reverse().find((e) => e.type === 'call');
          if (lastCall && !lastCall.result) {
            return prev.map((e) =>
              e.id === lastCall.id
                ? { ...e, result: event.payload.text }
                : e,
            );
          }
          return prev;
        });
      }
    });
    return unsubscribe;
  }, [onMessage]);

  return (
    <div className="border-t border-gray-700 bg-gray-900">
      {/* Header */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center gap-2 w-full px-4 py-2 text-sm text-gray-400 hover:text-gray-200 transition-colors"
      >
        <Wrench size={14} />
        <span>Tool Calls</span>
        <span className="text-xs text-gray-600">({entries.length})</span>
        <span className="ml-auto">
          {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </span>
      </button>

      {/* Entries */}
      {expanded && (
        <div className="max-h-48 overflow-y-auto px-4 pb-2 space-y-2">
          {entries.length === 0 && (
            <p className="text-xs text-gray-600 italic">No tool calls yet</p>
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
    <div className="bg-gray-800 rounded-lg text-xs">
      <button
        onClick={() => setShowDetails(!showDetails)}
        className="flex items-center gap-2 w-full px-3 py-2 text-left"
      >
        <span className="text-yellow-500 font-mono">{entry.name}</span>
        <span className="text-gray-600">{entry.timestamp}</span>
        {entry.result && (
          <span className="ml-auto text-green-500">done</span>
        )}
        <span className="ml-1">
          {showDetails ? (
            <ChevronDown size={12} />
          ) : (
            <ChevronRight size={12} />
          )}
        </span>
      </button>
      {showDetails && (
        <div className="px-3 pb-2 space-y-1 border-t border-gray-700">
          {entry.args && (
            <div>
              <span className="text-gray-500">Args: </span>
              <span className="text-gray-300 font-mono break-all">
                {entry.args}
              </span>
            </div>
          )}
          {entry.result && (
            <div>
              <span className="text-gray-500">Result: </span>
              <span className="text-gray-300 font-mono break-all">
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
