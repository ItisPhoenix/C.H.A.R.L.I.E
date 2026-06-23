import { useState, useCallback, useEffect } from 'react';
import { useWebSocket } from './hooks/useWebSocket';
import { ChatPanel } from './components/ChatPanel';
import { StatusBar } from './components/StatusBar';
import { ToolLog } from './components/ToolLog';
import { Sidebar } from './components/Sidebar';

function App() {
  const [status, setStatus] = useState('idle');
  const [currentSessionId, setCurrentSessionId] = useState(null);
  const [sessions, setSessions] = useState([]);
  const [loadingSessions, setLoadingSessions] = useState(false);
  const wsUrl = `ws://${window.location.hostname}:8000/ws`;
  const { send, onMessage, readyState } = useWebSocket(wsUrl);

  const fetchSessions = useCallback(async () => {
    setLoadingSessions(true);
    try {
      const res = await fetch('/api/sessions');
      const data = await res.json();
      const sessionList = data.sessions || [];
      setSessions(sessionList);
      
      // Auto-select first session if none selected
      if (sessionList.length > 0 && !currentSessionId) {
        setCurrentSessionId(sessionList[0].id);
      }
    } catch (err) {
      console.error('Failed to fetch sessions:', err);
    } finally {
      setLoadingSessions(false);
    }
  }, [currentSessionId]);

  useEffect(() => {
    fetchSessions();
  }, [fetchSessions]);

  // Update status based on events
  const wrappedOnMessage = useCallback(
    (handler) => {
      return onMessage((event) => {
        switch (event.type) {
          case 'vad_start':
            setStatus('listening');
            break;
          case 'thinking':
            setStatus('thinking');
            break;
          case 'speaking_start':
            setStatus('speaking');
            break;
          case 'speaking_stop':
          case 'response_done':
            setStatus('idle');
            break;
          default:
            break;
        }
        handler(event);
      });
    },
    [onMessage],
  );

  const handleSend = useCallback(
    (msg) => {
      send(msg);
    },
    [send],
  );

  const handleStop = useCallback(() => {
    send({ type: 'stop' });
    setStatus('idle');
  }, [send]);

  const handleNewChat = useCallback(async () => {
    try {
      const res = await fetch('/api/sessions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      });
      const data = await res.json();
      setCurrentSessionId(data.session_id);
      await fetchSessions();
    } catch (err) {
      console.error('Failed to create session:', err);
    }
  }, [fetchSessions]);

  const handleSelectSession = useCallback((sessionId) => {
    setCurrentSessionId(sessionId);
  }, []);

  return (
    <div className="h-screen flex flex-col bg-gray-900 text-gray-100">
      {/* Status Bar */}
      <StatusBar status={status} wsConnected={readyState === WebSocket.OPEN} />

      {/* Main Content */}
      <div className="flex flex-1 overflow-hidden">
        {/* Sidebar (hidden on mobile) */}
        <div className="hidden md:block w-64 shrink-0">
          <Sidebar
            sessions={sessions}
            loading={loadingSessions}
            onRefresh={fetchSessions}
            currentSessionId={currentSessionId}
            onSelectSession={handleSelectSession}
            onNewChat={handleNewChat}
          />
        </div>

        {/* Chat + Tools */}
        <div className="flex-1 flex flex-col min-w-0">
          {/* Chat Panel */}
          <div className="flex-1 overflow-hidden">
            <ChatPanel
              onMessage={wrappedOnMessage}
              onSend={handleSend}
              onStop={handleStop}
              status={status}
            />
          </div>

          {/* Tool Log */}
          <ToolLog onMessage={wrappedOnMessage} />
        </div>
      </div>
    </div>
  );
}

export default App;
