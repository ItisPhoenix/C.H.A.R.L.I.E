import React, { useState, useEffect } from 'react';

const SECTIONS = [
  { id: 'chat', label: 'Chat History', icon: '💬' },
  { id: 'memory', label: 'Memory Manager', icon: '🧠' },
  { id: 'research', label: 'Research Log', icon: '🔍' },
  { id: 'settings', label: 'Settings', icon: '⚙️' },
  { id: 'status', label: 'System Status', icon: '📊' },
  { id: 'voice', label: 'Voice Toggle', icon: '🎤' },
  { id: 'waveform', label: 'Live Waveform', icon: '📈' },
  { id: 'emotions', label: 'Emotion Log', icon: '😊' },
];

export default function Dashboard({ charlieState }) {
  const [activeSection, setActiveSection] = useState('chat');
  const [chatHistory, setChatHistory] = useState([]);
  const [memories, setMemories] = useState([]);
  const [systemInfo, setSystemInfo] = useState({ cpu: 0, gpu: 0, memory: 0 });
  const [voiceMode, setVoiceMode] = useState('always-on');
  const [emotions, setEmotions] = useState([]);

  // System info polling
  useEffect(() => {
    const interval = setInterval(() => {
      // In production, this would get real system info from Node.js
      setSystemInfo({
        cpu: Math.random() * 30 + 10,
        gpu: Math.random() * 40 + 5,
        memory: Math.random() * 20 + 40,
      });
    }, 2000);
    return () => clearInterval(interval);
  }, []);

  const renderSection = () => {
    switch (activeSection) {
      case 'chat':
        return (
          <div style={styles.section}>
            <h3 style={styles.sectionTitle}>Chat History</h3>
            <div style={styles.scrollArea}>
              {chatHistory.length === 0 ? (
                <p style={styles.emptyText}>No messages yet. Start chatting with Charlie!</p>
              ) : (
                chatHistory.map((msg, i) => (
                  <div key={i} style={styles.message}>
                    <span style={styles.msgRole}>{msg.role}:</span> {msg.text}
                  </div>
                ))
              )}
            </div>
          </div>
        );

      case 'memory':
        return (
          <div style={styles.section}>
            <h3 style={styles.sectionTitle}>Memory Manager</h3>
            <input
              type="text"
              placeholder="Search memories..."
              style={styles.input}
            />
            <div style={styles.scrollArea}>
              {memories.length === 0 ? (
                <p style={styles.emptyText}>No memories stored yet.</p>
              ) : (
                memories.map((mem, i) => (
                  <div key={i} style={styles.memoryItem}>
                    <span>{mem.text}</span>
                    <button style={styles.deleteBtn}>×</button>
                  </div>
                ))
              )}
            </div>
          </div>
        );

      case 'research':
        return (
          <div style={styles.section}>
            <h3 style={styles.sectionTitle}>Research Log</h3>
            <div style={styles.scrollArea}>
              <p style={styles.emptyText}>No research sessions yet.</p>
            </div>
          </div>
        );

      case 'settings':
        return (
          <div style={styles.section}>
            <h3 style={styles.sectionTitle}>Settings</h3>
            <div style={styles.settingGroup}>
              <label style={styles.label}>VAD Threshold</label>
              <input type="range" min="0" max="100" defaultValue="50" style={styles.slider} />
            </div>
            <div style={styles.settingGroup}>
              <label style={styles.label}>Voice Model</label>
              <select style={styles.select}>
                <option value="kokoro">Kokoro ONNX</option>
              </select>
            </div>
            <div style={styles.settingGroup}>
              <label style={styles.label}>Personality</label>
              <select style={styles.select}>
                <option value="default">Default</option>
                <option value="concise">Concise</option>
                <option value="verbose">Verbose</option>
              </select>
            </div>
          </div>
        );

      case 'status':
        return (
          <div style={styles.section}>
            <h3 style={styles.sectionTitle}>System Status</h3>
            <div style={styles.statusGrid}>
              <div style={styles.statusItem}>
                <span style={styles.statusLabel}>CPU</span>
                <div style={styles.progressBg}>
                  <div style={{ ...styles.progressFill, width: `${systemInfo.cpu}%` }} />
                </div>
                <span style={styles.statusValue}>{systemInfo.cpu.toFixed(1)}%</span>
              </div>
              <div style={styles.statusItem}>
                <span style={styles.statusLabel}>GPU</span>
                <div style={styles.progressBg}>
                  <div style={{ ...styles.progressFill, width: `${systemInfo.gpu}%`, background: '#48bb78' }} />
                </div>
                <span style={styles.statusValue}>{systemInfo.gpu.toFixed(1)}%</span>
              </div>
              <div style={styles.statusItem}>
                <span style={styles.statusLabel}>RAM</span>
                <div style={styles.progressBg}>
                  <div style={{ ...styles.progressFill, width: `${systemInfo.memory}%`, background: '#4299e1' }} />
                </div>
                <span style={styles.statusValue}>{systemInfo.memory.toFixed(1)}%</span>
              </div>
            </div>
            <div style={styles.stateIndicator}>
              <span>Charlie State: </span>
              <span style={{ ...styles.stateBadge, background: getStateColor(charlieState) }}>
                {charlieState}
              </span>
            </div>
          </div>
        );

      case 'voice':
        return (
          <div style={styles.section}>
            <h3 style={styles.sectionTitle}>Voice Mode</h3>
            <div style={styles.toggleGroup}>
              <button
                style={{ ...styles.toggleBtn, ...(voiceMode === 'always-on' ? styles.toggleActive : {}) }}
                onClick={() => setVoiceMode('always-on')}
              >
                Always On
              </button>
              <button
                style={{ ...styles.toggleBtn, ...(voiceMode === 'push-to-talk' ? styles.toggleActive : {}) }}
                onClick={() => setVoiceMode('push-to-talk')}
              >
                Push to Talk
              </button>
            </div>
          </div>
        );

      case 'waveform':
        return (
          <div style={styles.section}>
            <h3 style={styles.sectionTitle}>Live Waveform</h3>
            <div style={styles.waveformContainer}>
              <canvas style={styles.waveformCanvas} />
              <p style={styles.emptyText}>Waveform visualization active when speaking.</p>
            </div>
          </div>
        );

      case 'emotions':
        return (
          <div style={styles.section}>
            <h3 style={styles.sectionTitle}>Emotion Log</h3>
            <div style={styles.scrollArea}>
              {emotions.length === 0 ? (
                <p style={styles.emptyText}>No emotions detected yet.</p>
              ) : (
                emotions.map((emo, i) => (
                  <div key={i} style={styles.emotionItem}>
                    <span>{emo.time}</span> - <span>{emo.emotion}</span>
                  </div>
                ))
              )}
            </div>
          </div>
        );

      default:
        return null;
    }
  };

  return (
    <div style={styles.dashboard}>
      {/* Sidebar */}
      <div style={styles.sidebar}>
        <div style={styles.logo}>🤖 Charlie</div>
        {SECTIONS.map((sec) => (
          <button
            key={sec.id}
            style={{
              ...styles.navBtn,
              ...(activeSection === sec.id ? styles.navBtnActive : {}),
            }}
            onClick={() => setActiveSection(sec.id)}
          >
            <span>{sec.icon}</span>
            <span>{sec.label}</span>
          </button>
        ))}
        <button
          style={styles.collapseBtn}
          onClick={() => window.electronAPI?.toggleExpand()}
        >
          ← Collapse
        </button>
      </div>

      {/* Main content */}
      <div style={styles.main}>{renderSection()}</div>
    </div>
  );
}

function getStateColor(state) {
  const colors = {
    idle: '#a0aec0',
    listening: '#48bb78',
    thinking: '#4299e1',
    speaking: '#ed8936',
    happy: '#ffd700',
    curious: '#9f7aea',
    confused: '#fc8181',
    sleepy: '#718096',
  };
  return colors[state] || '#a0aec0';
}

const styles = {
  dashboard: {
    display: 'flex',
    width: '100%',
    height: '100%',
    background: '#1a202c',
    color: '#e2e8f0',
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
  },
  sidebar: {
    width: 200,
    background: '#2d3748',
    display: 'flex',
    flexDirection: 'column',
    padding: '16px 0',
    borderRight: '1px solid #4a5568',
  },
  logo: {
    padding: '0 16px 16px',
    fontSize: 18,
    fontWeight: 'bold',
    borderBottom: '1px solid #4a5568',
    marginBottom: 8,
  },
  navBtn: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    padding: '10px 16px',
    background: 'transparent',
    border: 'none',
    color: '#a0aec0',
    cursor: 'pointer',
    textAlign: 'left',
    fontSize: 14,
    transition: 'all 0.2s',
  },
  navBtnActive: {
    background: '#4a5568',
    color: '#fff',
  },
  collapseBtn: {
    marginTop: 'auto',
    padding: '10px 16px',
    background: 'transparent',
    border: 'none',
    color: '#a0aec0',
    cursor: 'pointer',
    textAlign: 'left',
    fontSize: 14,
    borderTop: '1px solid #4a5568',
  },
  main: {
    flex: 1,
    padding: 20,
    overflow: 'hidden',
  },
  section: {
    height: '100%',
    display: 'flex',
    flexDirection: 'column',
  },
  sectionTitle: {
    fontSize: 20,
    fontWeight: 'bold',
    marginBottom: 16,
    color: '#fff',
  },
  scrollArea: {
    flex: 1,
    overflowY: 'auto',
    padding: 8,
    background: '#2d3748',
    borderRadius: 8,
  },
  emptyText: {
    color: '#718096',
    fontStyle: 'italic',
    textAlign: 'center',
    marginTop: 40,
  },
  message: {
    padding: '8px 12px',
    borderBottom: '1px solid #4a5568',
  },
  msgRole: {
    fontWeight: 'bold',
    color: '#4299e1',
  },
  input: {
    width: '100%',
    padding: '8px 12px',
    borderRadius: 6,
    border: '1px solid #4a5568',
    background: '#2d3748',
    color: '#e2e8f0',
    marginBottom: 12,
    fontSize: 14,
  },
  memoryItem: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: '8px 12px',
    borderBottom: '1px solid #4a5568',
  },
  deleteBtn: {
    background: '#fc8181',
    border: 'none',
    borderRadius: 4,
    color: '#fff',
    cursor: 'pointer',
    padding: '2px 8px',
  },
  settingGroup: {
    marginBottom: 16,
  },
  label: {
    display: 'block',
    marginBottom: 6,
    color: '#a0aec0',
    fontSize: 14,
  },
  slider: {
    width: '100%',
  },
  select: {
    width: '100%',
    padding: '8px 12px',
    borderRadius: 6,
    border: '1px solid #4a5568',
    background: '#2d3748',
    color: '#e2e8f0',
    fontSize: 14,
  },
  statusGrid: {
    display: 'flex',
    flexDirection: 'column',
    gap: 16,
  },
  statusItem: {
    display: 'flex',
    alignItems: 'center',
    gap: 12,
  },
  statusLabel: {
    width: 40,
    fontSize: 14,
    color: '#a0aec0',
  },
  progressBg: {
    flex: 1,
    height: 8,
    background: '#4a5568',
    borderRadius: 4,
    overflow: 'hidden',
  },
  progressFill: {
    height: '100%',
    background: '#ed8936',
    borderRadius: 4,
    transition: 'width 0.5s ease',
  },
  statusValue: {
    width: 50,
    textAlign: 'right',
    fontSize: 14,
    color: '#e2e8f0',
  },
  stateIndicator: {
    marginTop: 24,
    padding: 12,
    background: '#2d3748',
    borderRadius: 8,
    fontSize: 14,
  },
  stateBadge: {
    padding: '2px 8px',
    borderRadius: 4,
    color: '#fff',
    fontWeight: 'bold',
  },
  toggleGroup: {
    display: 'flex',
    gap: 12,
  },
  toggleBtn: {
    flex: 1,
    padding: '12px 24px',
    borderRadius: 8,
    border: '2px solid #4a5568',
    background: 'transparent',
    color: '#a0aec0',
    cursor: 'pointer',
    fontSize: 14,
    fontWeight: 'bold',
    transition: 'all 0.2s',
  },
  toggleActive: {
    borderColor: '#48bb78',
    background: '#48bb7820',
    color: '#48bb78',
  },
  waveformContainer: {
    flex: 1,
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    justifyContent: 'center',
  },
  waveformCanvas: {
    width: '100%',
    height: 200,
    background: '#2d3748',
    borderRadius: 8,
  },
  emotionItem: {
    padding: '8px 12px',
    borderBottom: '1px solid #4a5568',
    fontSize: 14,
  },
};