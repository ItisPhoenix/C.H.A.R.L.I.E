import { useEffect, useState, useCallback } from 'react';
import { Orb } from './components/Orb';
import { CompanionWS } from './services/ws';

const WS_TOKEN = '';

function App() {
  const [status, setStatus] = useState(0);
  const [toast, setToast] = useState<string | null>(null);
  const [contextMenu, setContextMenu] = useState<{ x: number; y: number } | null>(null);
  const [ws] = useState(() => new CompanionWS(setStatus, (msg) => {
    setToast(msg);
    setTimeout(() => setToast(null), 3000);
  }));

  useEffect(() => {
    ws.connect(WS_TOKEN);
    return () => ws.disconnect();
  }, [ws]);

  const handleDrag = useCallback(async () => {
    try {
      const { invoke } = await import('@tauri-apps/api/core');
      await invoke('drag_window');
    } catch { /* not in Tauri */ }
  }, []);

  const handleContextMenu = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    setContextMenu({ x: e.clientX, y: e.clientY });
  }, []);

  const closeContextMenu = useCallback(() => {
    setContextMenu(null);
  }, []);

  const handleMute = useCallback(() => {
    ws.send({ type: 'MUTE' });
    setToast('🔇 Muted');
    setTimeout(() => setToast(null), 2000);
    closeContextMenu();
  }, [ws, closeContextMenu]);

  const handleStandby = useCallback(() => {
    ws.send({ type: 'STANDBY' });
    setToast('💤 Standby');
    setTimeout(() => setToast(null), 2000);
    closeContextMenu();
  }, [ws, closeContextMenu]);

  const handleSettings = useCallback(() => {
    window.open('http://127.0.0.1:8090/settings', '_blank');
    closeContextMenu();
  }, [closeContextMenu]);

  const handleQuit = useCallback(() => {
    ws.send({ type: 'SHUTDOWN' });
    closeContextMenu();
    window.close();
  }, [ws, closeContextMenu]);

  // Close context menu on click outside
  useEffect(() => {
    if (!contextMenu) return;
    const handler = () => closeContextMenu();
    window.addEventListener('click', handler);
    return () => window.removeEventListener('click', handler);
  }, [contextMenu, closeContextMenu]);

  return (
    <div
      style={{
        width: '100vw',
        height: '100vh',
        background: 'transparent',
        position: 'relative',
        userSelect: 'none',
      }}
      onContextMenu={handleContextMenu}
    >
      <Orb status={status} onDrag={handleDrag} />
      {toast && (
        <div style={{
          position: 'fixed',
          bottom: 20,
          left: '50%',
          transform: 'translateX(-50%)',
          background: 'rgba(0,0,0,0.8)',
          color: '#fff',
          padding: '8px 16px',
          borderRadius: 8,
          fontSize: 13,
          whiteSpace: 'nowrap',
          pointerEvents: 'none',
          zIndex: 100,
        }}>
          {toast}
        </div>
      )}
      {contextMenu && (
        <div
          style={{
            position: 'fixed',
            left: contextMenu.x,
            top: contextMenu.y,
            background: '#1a1a1a',
            border: '1px solid #333',
            borderRadius: 8,
            padding: '4px 0',
            minWidth: 140,
            boxShadow: '0 4px 20px rgba(0,0,0,0.5)',
            zIndex: 200,
          }}
        >
          <MenuItem label="🔇 Mute" onClick={handleMute} />
          <MenuItem label="💤 Standby" onClick={handleStandby} />
          <div style={{ height: 1, background: '#333', margin: '4px 8px' }} />
          <MenuItem label="⚙ Settings" onClick={handleSettings} />
          <div style={{ height: 1, background: '#333', margin: '4px 8px' }} />
          <MenuItem label="✕ Quit" onClick={handleQuit} />
        </div>
      )}
    </div>
  );
}

function MenuItem({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <div
      onClick={onClick}
      style={{
        padding: '6px 16px',
        cursor: 'pointer',
        color: '#ccc',
        fontSize: 13,
        whiteSpace: 'nowrap',
      }}
      onMouseEnter={(e) => { e.currentTarget.style.background = '#333'; }}
      onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent'; }}
    >
      {label}
    </div>
  );
}

export default App;
