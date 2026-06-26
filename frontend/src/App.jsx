import { useState, useCallback, useEffect } from 'react';
import { useWebSocket } from './hooks/useWebSocket';
import { ChatPanel } from './components/ChatPanel';
import { StatusBar } from './components/StatusBar';
import { Sidebar } from './components/Sidebar';
import { VoiceDock } from './components/VoiceDock';
import { SmartPanel } from './components/SmartPanel';
import { AnimatePresence } from 'framer-motion';

function useMediaQuery(query) {
  const [matches, setMatches] = useState(() => window.matchMedia(query).matches);
  useEffect(() => {
    const mql = window.matchMedia(query);
    const handler = (e) => setMatches(e.matches);
    mql.addEventListener('change', handler);
    return () => mql.removeEventListener('change', handler);
  }, [query]);
  return matches;
}

function App() {
  const [status, setStatus] = useState('idle');
  const [currentSessionId, setCurrentSessionId] = useState(null);
  const [sessions, setSessions] = useState([]);
  const [loadingSessions, setLoadingSessions] = useState(false);
  const [initialMessages, setInitialMessages] = useState([]);
  const [launchId, setLaunchId] = useState(null);
  const [filterMode, setFilterMode] = useState('launch');
  const [smartPanelVisible, setSmartPanelVisible] = useState(true);
  const [wakeWordPulse, setWakeWordPulse] = useState(false);

  const isDesktop = useMediaQuery('(min-width: 1024px)');
  const isTablet = useMediaQuery('(min-width: 768px) and (max-width: 1023px)');
  const isUnsupported = useMediaQuery('(max-width: 767px)');

  const sidebarCollapsed = isTablet;

  // Close smart panel on tablet if it was open
  useEffect(() => {
    if (isTablet) setSmartPanelVisible(false);
    if (isDesktop) setSmartPanelVisible(true);
  }, [isTablet, isDesktop]);

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
      // Auto-select first session only when none is selected (use functional update to avoid stale closure)
      setCurrentSessionId((prev) => {
        if (prev === null && sessionList.length > 0) {
          return sessionList[0].id;
        }
        return prev;
      });
    } catch (err) {
      console.error('Failed to fetch sessions:', err);
    } finally {
      setLoadingSessions(false);
    }
  }, [filterMode, launchId]);

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

  // Notify backend of the active session so voice/speech gets routed correctly
  useEffect(() => {
    if (readyState === WebSocket.OPEN && currentSessionId) {
      send({ type: 'session_active', session_id: currentSessionId });
    }
  }, [currentSessionId, readyState, send]);

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
          case 'wake_word':
            // Trigger 1.5s pulse animation on mic indicator
            setWakeWordPulse(true);
            setTimeout(() => setWakeWordPulse(false), 1500);
            break;
          case 'session_update':
            if (event.payload && event.payload.deleted) {
              const deletedId = event.payload.session_id;
              setCurrentSessionId((prev) => (prev === deletedId ? null : prev));
            }
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

  const handleDeleteSession = useCallback(async (sessionId) => {
    try {
      await fetch(`/api/sessions/${sessionId}`, { method: 'DELETE' });
      // Set to null first so fetchSessions auto-selects the first remaining session
      setCurrentSessionId((prev) => (prev === sessionId ? null : prev));
      await fetchSessions();
    } catch (err) {
      console.error('Failed to delete session:', err);
    }
  }, [fetchSessions]);

  const toggleSmartPanel = useCallback(() => {
    setSmartPanelVisible((prev) => !prev);
  }, []);

  // Unsupported screen
  if (isUnsupported) {
    return (
      <div className="h-screen flex items-center justify-center bg-[var(--surface)]">
        <p className="text-sm text-[var(--text-muted)] text-center px-8">
          Charlie requires a tablet or desktop screen.
        </p>
      </div>
    );
  }

  return (
    <div className="h-screen flex flex-col bg-[var(--surface)] text-[var(--text-primary)]">
      <StatusBar status={status} wsConnected={readyState === WebSocket.OPEN} />

      <div className="flex flex-1 overflow-hidden min-h-0">
        {/* Sidebar */}
        <Sidebar
          sessions={sessions}
          loading={loadingSessions}
          onRefresh={fetchSessions}
          currentSessionId={currentSessionId}
          onSelectSession={handleSelectSession}
          onNewChat={handleNewChat}
          filterMode={filterMode}
          onFilterModeChange={setFilterMode}
          collapsed={sidebarCollapsed}
          onDeleteSession={handleDeleteSession}
        />

        {/* Center: Chat + Voice Dock */}
        <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
          <div className="flex-1 overflow-hidden">
            <ChatPanel
              onMessage={wrappedOnMessage}
              onSend={handleSend}
              onStop={handleStop}
              status={status}
              currentSessionId={currentSessionId}
              initialMessages={initialMessages}
              onToggleSmartPanel={toggleSmartPanel}
            />
          </div>
          <VoiceDock status={status} wakeWordPulse={wakeWordPulse} />
        </div>

        {/* Right: Smart Panel */}
        <AnimatePresence>
          {smartPanelVisible && isDesktop && (
            <SmartPanel visible={smartPanelVisible} onClose={toggleSmartPanel} onMessage={wrappedOnMessage} currentSessionId={currentSessionId} />
          )}
        </AnimatePresence>
      </div>
    </div>
  );
}

export default App;
