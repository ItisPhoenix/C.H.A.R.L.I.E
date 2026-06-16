import React, { useState, useEffect } from 'react';
import { io } from 'socket.io-client';
import BuddyRobot from './components/BuddyRobot';
import Dashboard from './components/Dashboard';

export default function App() {
  const [mode, setMode] = useState('compact');
  const [charlieState, setCharlieState] = useState('idle');
  const [mouthValue, setMouthValue] = useState(0.0);
  const [connected, setConnected] = useState(false);
  const [lastText, setLastText] = useState('');
  const [emotion, setEmotion] = useState('neutral');

  useEffect(() => {
    if (window.electronAPI) {
      window.electronAPI.onModeChange((newMode) => setMode(newMode));
    }
  }, []);

  // Socket.IO connection to Charlie
  useEffect(() => {
    const socket = io('http://127.0.0.1:8765', {
      transports: ['websocket', 'polling'],
      reconnection: true,
      reconnectionDelay: 1000,
      reconnectionAttempts: 10,
    });

    socket.on('connect', () => {
      console.log('[Buddy] Connected to Charlie bridge');
      setConnected(true);
    });

    socket.on('disconnect', (reason) => {
      console.log('[Buddy] Disconnected:', reason);
      setConnected(false);
    });

    socket.on('charlie_state', (data) => {
      console.log('[Buddy] State received:', data);
      if (data?.state) setCharlieState(data.state);
      if (data?.mouth_value !== undefined) setMouthValue(data.mouth_value);
    });
    socket.on('charlie_text', (data) => {
      console.log('[Buddy] Text received:', data);
      if (data?.text) setLastText(data.text);
    });
    socket.on('charlie_emotion', (data) => {
      console.log('[Buddy] Emotion received:', data);
      if (data?.emotion) setEmotion(data.emotion);
    });

    socket.on('connect_error', (err) => {
      console.log('[Buddy] Connection error:', err.message);
    });

    return () => socket.close();
  }, []);

  return (
    <div style={{ width: '100%', height: '100%', background: 'transparent' }}>
      {mode === 'compact' ? (
        <BuddyRobot state={charlieState} mouthValue={mouthValue} lastText={lastText} emotion={emotion} />
      ) : (
        <Dashboard charlieState={charlieState} />
      )}
    </div>
  );
}