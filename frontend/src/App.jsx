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
  const [initialMessages, setInitialMessages] = useState([]);
  const [launchId, setLaunchId] = useState(null);
  const [filterMode, setFilterMode] = useState('launch'); // 'launch' or 'all'
  const wsUrl = `ws://${window.location.hostname}:8000/ws`;
  const { send, onMessage, readyState } = useWebSocket(wsUrl);

  // Fetch launch_id from backend on mount
  useEffect(() => {
    fetch('/api/status')
      .then((res) => res.json())
      .then((data) => {
        if (data.launch_id) setLaunchId(data.launch_id);
      })
      .catch(() => {});
  }, []);

  const fetchSessions = useCallback(async () => {
    setLoadingSessions(true);
    try {
      let url = '/api/sessions';
      if (filterMode === 'launch' && launchId) {
        url += `?launch_id=${encodeURIComponent(launchId)}`;
      }
      const res = await fetch(url);
      const data = await res.json();
      const sessionList = data.sessions || [];
      setSessions(sessionList);

      if (sessionList.length > 0 && !currentSessionId) {
        setCurrentSessionId(sessionList[0].id);
      }
    } catch (err) {
      console.error('Failed to fetch sessions:', err);
    } finally {
      setLoadingSessions(false);
    }
  }, [currentSessionId, filterMode, launchId]);

  const fetchMessages = useCallback(async () => {
    if (!currentSessionId) {
      return;
    }
    try {
      const res = await fetch(`/api/sessions/${encodeURIComponent(currentSessionId)}/messages`);
      const data = await res.json();
      const normalized = (data.messages || []).map((m) => ({
        role: String(m.role ?? m.role),
        content: typeof m.content === 'string' ? m.content : '',
      }));
      setInitialMessages(normalized);
    } catch (err) {
      console.error('Failed to fetch session messages:', err);
    }
  }, [currentSessionId]);

  useEffect(() => {
    fetchSessions();
    const timer = setInterval(fetchSessions, 5000);
    return () => clearInterval(timer);
  }, [fetchSessions]);

  useEffect(() => {
    if (!currentSessionId) {
      setInitialMessages([]);
      return;
    }
    fetchMessages();
  }, [currentSessionId, fetchMessages]);

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
          case 'session_update':
            // Title or metadata changed -- refresh sidebar immediately
            fetchSessions();
            break;
          default:
            break;
        }
        handler(event);
      });
    },
    [onMessage, fetchSessions],
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
        body: JSON.stringify({ launch_id: launchId, source: 'web' }),
      });
      const data = await res.json();
      setCurrentSessionId(data.session_id);
      await fetchSessions();
    } catch (err) {
      console.error('Failed to create session:', err);
    }
  }, [fetchSessions, launchId]);

  const handleSelectSession = useCallback((sessionId) => {
    setCurrentSessionId(sessionId);
  }, []);

  return (
    <div className="h-screen flex flex-col bg-[var(--surface)] text-[var(--text-primary)]">
      <StatusBar status={status} wsConnected={readyState === WebSocket.OPEN} />

      <div className="flex flex-1 overflow-hidden">
        <div className="hidden md:block w-72 shrink-0">
          <Sidebar
            sessions={sessions}
            loading={loadingSessions}
            onRefresh={fetchSessions}
            currentSessionId={currentSessionId}
            onSelectSession={handleSelectSession}
            onNewChat={handleNewChat}
            filterMode={filterMode}
            onFilterModeChange={setFilterMode}
          />
        </div>

        <div className="flex-1 flex flex-col min-w-0">
          <div className="flex-1 overflow-hidden">
            <ChatPanel
              onMessage={wrappedOnMessage}
              onSend={handleSend}
              onStop={handleStop}
              status={status}
              currentSessionId={currentSessionId}
              initialMessages={initialMessages}
            />
          </div>

          <ToolLog onMessage={wrappedOnMessage} currentSessionId={currentSessionId} />
        </div>
      </div>
    </div>
  );
}

export default App;
