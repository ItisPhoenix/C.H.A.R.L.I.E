import { Mic, Brain, Volume2, Wifi, WifiOff } from 'lucide-react';

const STATUS_CONFIG = {
  idle: { label: 'Idle', color: 'bg-gray-500', icon: Wifi },
  listening: { label: 'Listening...', color: 'bg-green-500', icon: Mic },
  thinking: { label: 'Thinking...', color: 'bg-yellow-500', icon: Brain },
  speaking: { label: 'Speaking...', color: 'bg-blue-500', icon: Volume2 },
};

export function StatusBar({ status, wsConnected }) {
  const config = STATUS_CONFIG[status] || STATUS_CONFIG.idle;
  const Icon = config.icon;

  return (
    <div className="flex items-center gap-3 px-4 py-2 bg-gray-800 border-b border-gray-700">
      {/* Status indicator */}
      <div className="flex items-center gap-2">
        <div className={`w-3 h-3 rounded-full ${config.color} animate-pulse`} />
        <Icon size={16} className="text-gray-400" />
        <span className="text-sm text-gray-300">{config.label}</span>
      </div>

      {/* WebSocket connection */}
      <div className="ml-auto flex items-center gap-1">
        {wsConnected ? (
          <>
            <Wifi size={14} className="text-green-500" />
            <span className="text-xs text-gray-500">Connected</span>
          </>
        ) : (
          <>
            <WifiOff size={14} className="text-red-500" />
            <span className="text-xs text-gray-500">Disconnected</span>
          </>
        )}
      </div>
    </div>
  );
}
