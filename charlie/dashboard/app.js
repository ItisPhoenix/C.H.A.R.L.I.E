/**
 * CHARLIE Dashboard — Vanilla JS SPA
 * Phase 9 replacement for Next.js dashboard.
 */

// ── State ────────────────────────────────────────────────────────────────────

const state = {
  currentPage: 'home',
  status: {},
  chatHistory: [],
  agents: [],
  skills: [],
  tools: [],
};

// ── Nav ──────────────────────────────────────────────────────────────────────

function navigate(page) {
  window.location.hash = page;
}

function setupNav() {
  document.querySelectorAll('.nav-link').forEach(link => {
    link.addEventListener('click', e => {
      e.preventDefault();
      const page = link.dataset.page;
      navigate(page);
    });
  });

  window.addEventListener('hashchange', () => {
    const page = window.location.hash.slice(1) || 'home';
    showPage(page);
  });

  // Init from URL
  const page = window.location.hash.slice(1) || 'home';
  showPage(page);
}

function showPage(page) {
  // Hide all pages
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.nav-link').forEach(l => l.classList.remove('active'));

  // Show target
  const el = document.getElementById(`page-${page}`);
  if (el) el.classList.add('active');

  const navLink = document.querySelector(`.nav-link[data-page="${page}"]`);
  if (navLink) navLink.classList.add('active');

  state.currentPage = page;

  // Refresh page data
  if (page === 'home')    loadHome();
  if (page === 'chat')    loadChat();
  if (page === 'tasks')   loadTasks();
  if (page === 'agents') loadAgents();
  if (page === 'skills') loadSkills();
  if (page === 'tools')  loadTools();
  if (page === 'globe')  loadGlobe();
}

// ── API helpers ───────────────────────────────────────────────────────────────

async function api(path, options = {}) {
  const resp = await fetch(`/api/${path}`, options);
  if (!resp.ok) throw new Error(`${resp.status}: ${await resp.text()}`);
  const ct = resp.headers.get('content-type') || '';
  return ct.includes('json') ? resp.json() : resp.text();
}

async function pollStatus() {
  try {
    state.status = await api('status') || {};
    updateDaemonStatus(true);
  } catch {
    updateDaemonStatus(false);
  }
  // Refresh chat history if on chat page
  if (document.getElementById('messages')) {
    try {
      const history = await api('chat/history');
      const oldLast = state.chatHistory[state.chatHistory.length - 1]?.content;
      const newLast = history?.[history.length - 1]?.content;
      if (history && newLast !== oldLast) {
        state.chatHistory = history;
        renderMessages();
      }
    } catch {}
  }
}

function updateDaemonStatus(online) {
  const el = document.getElementById('daemon-status');
  if (!el) return;
  const dot = el.querySelector('.status-dot');
  if (dot) {
    dot.className = 'status-dot ' + (online ? 'online' : 'offline');
  }
  el.querySelector('span:last-child').textContent = online ? 'Daemon Online' : 'Daemon Offline';
}

// ── Home ─────────────────────────────────────────────────────────────────────

async function loadHome() {
  await pollStatus();

  document.getElementById('mood-display').textContent =
    state.status.avatar?.mood || 'idle';
  document.getElementById('brain-status').textContent =
    state.status.daemon?.status || '—';
  document.getElementById('task-count').textContent =
    state.status.daemon?.active_tasks ?? '—';
  document.getElementById('model-name').textContent =
    state.status.daemon?.model || '—';
}

// ── Chat ──────────────────────────────────────────────────────────────────────

async function loadChat() {
  try {
    state.chatHistory = await api('chat/history') || [];
  } catch {
    state.chatHistory = [];
  }
  renderMessages();
}

function renderMessages() {
  const el = document.getElementById('messages');
  if (!el) return;
  el.innerHTML = '';
  for (const msg of state.chatHistory.slice(-50)) {
    const div = document.createElement('div');
    div.className = `msg ${msg.role === 'user' ? 'user' : 'assistant'}`;
    div.textContent = msg.content;
    el.appendChild(div);
  }
  el.scrollTop = el.scrollHeight;
}

async function sendChat() {
  const input = document.getElementById('chat-input');
  const text = input.value.trim();
  if (!text) return;
  input.value = '';

  try {
    await api('chat/message', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: text }),
    });
    // Optimistic update
    state.chatHistory.push({ role: 'user', content: text });
    renderMessages();

    // Poll for Brain's response (up to 30s)
    const initialLen = state.chatHistory.length;
    for (let i = 0; i < 30; i++) {
      await new Promise(r => setTimeout(r, 1000));
      const history = await api('chat/history');
      if (history && history.length > initialLen) {
        state.chatHistory = history;
        renderMessages();
        break;
      }
    }
  } catch (e) {
    console.error('sendChat failed:', e);
  }
}

// ── Tasks ─────────────────────────────────────────────────────────────────────

async function loadTasks() {
  const el = document.getElementById('task-list');
  if (!el) return;
  await pollStatus();
  const tasks = state.status.daemon?.tasks || [];
  if (!tasks.length) {
    el.innerHTML = '<div class="loading">No active tasks</div>';
    return;
  }
  el.innerHTML = tasks.map(t => `
    <div class="task-item" style="padding:8px 0;border-bottom:1px solid rgba(0,212,255,0.06)">
      <span style="color:#e0e0f0">${t}</span>
    </div>
  `).join('');
}

// ── Globe ─────────────────────────────────────────────────────────────────────
let _globeApp = null;

async function loadGlobe() {
  if (!window.GlobeApp) {
    document.getElementById('globe-container').innerHTML =
      '<div class="loading">Loading Globe...</div>';
    // Globe.js loaded as module, wait for it
    await new Promise(r => setTimeout(r, 200));
  }
  if (window.GlobeApp) {
    if (_globeApp) _globeApp.destroy();
    _globeApp = new GlobeApp(document.getElementById('globe-container'));
    _globeApp.init().catch(console.error);
  }
}

// ── Agents ────────────────────────────────────────────────────────────────────

async function loadAgents() {
  const el = document.getElementById('agent-list');
  if (!el) return;
  try {
    state.agents = await api('agents') || [];
  } catch {
    state.agents = [];
  }
  if (!state.agents.length) {
    el.innerHTML = '<div class="loading">No agents loaded</div>';
    return;
  }
  el.innerHTML = state.agents.map(a => `
    <div class="card">
      <h3>${a.name || a}</h3>
      <p style="font-size:13px;color:#8090a0;margin-top:8px">${a.description || ''}</p>
    </div>
  `).join('');
}

// ── Skills ────────────────────────────────────────────────────────────────────

async function loadSkills() {
  const el = document.getElementById('skill-list');
  if (!el) return;
  try {
    state.skills = await api('skills') || [];
  } catch {
    state.skills = [];
  }
  if (!state.skills.length) {
    el.innerHTML = '<div class="loading">No skills loaded</div>';
    return;
  }
  el.innerHTML = state.skills.map(s => `
    <div class="card">
      <h3>${s.name || s}</h3>
      <p style="font-size:13px;color:#8090a0;margin-top:8px">${s.description || ''}</p>
    </div>
  `).join('');
}

// ── Tools ────────────────────────────────────────────────────────────────────

async function loadTools() {
  const el = document.getElementById('tool-list');
  if (!el) return;
  try {
    const data = await api('tools') || {};
    state.tools = data.tools || [];
  } catch {
    state.tools = [];
  }
  if (!state.tools.length) {
    el.innerHTML = '<div class="loading">No tools registered</div>';
    return;
  }
  el.innerHTML = state.tools.map(t => `
    <div class="card">
      <h3>${t.name}</h3>
      <p style="font-size:12px;color:#8090a0;margin-top:8px">${t.description || ''}</p>
    </div>
  `).join('');
}

// ── Settings ──────────────────────────────────────────────────────────────────

function saveSettings() {
  const model = document.getElementById('setting-model').value;
  alert(`Settings saved (model: ${model}) — restart daemon to apply.`);
}

// ── WebSocket — avatar ────────────────────────────────────────────────────────

let avatarWs = null;
let avatarTargetMood = null;

function connectAvatarWS() {
  try {
    avatarWs = new WebSocket('ws://localhost:8090/ws/avatar');
    avatarWs.onopen = () => console.log('[avatar WS] connected');
    avatarWs.onmessage = ev => {
      try {
        const msg = JSON.parse(ev.data);
        if (msg.type === 'AVATAR_STATE') {
          updateAvatarState(msg);
        }
      } catch {}
    };
    avatarWs.onclose = () => {
      setTimeout(connectAvatarWS, 3000);
    };
  } catch {
    setTimeout(connectAvatarWS, 5000);
  }
}

function updateAvatarState(msg) {
  const canvas = document.getElementById('avatar-canvas');
  if (!canvas) return;

  if (!avatarTargetMood) {
    canvas.classList.add('visible');
  }
  avatarTargetMood = msg.mood;

  // Init avatar renderer if not already
  if (!window._avatarRenderer) {
    window._avatarRenderer = new DashboardAvatar('avatar-canvas');
  }
  window._avatarRenderer.onAvatarState(msg);
}

// ── Avatar Canvas Renderer ────────────────────────────────────────────────────

class DashboardAvatar {
  constructor(canvasId) {
    this.canvas = document.getElementById(canvasId);
    if (!this.canvas) return;
    this.ctx = this.canvas.getContext('2d');

    // Current interpolated state
    this.currentGeo = {
      head_tilt_x: 0, head_tilt_y: 0, head_tilt_z: 0,
      eye_gaze_x: 0, eye_gaze_y: 0,
      left_eyebrow_raise: 0, right_eyebrow_raise: 0,
      mouth_shape: 'NEUTRAL', blink: false,
    };
    this.targetGeo = { ...this.currentGeo };
    this.mood = 'idle';
    this.lastFrame = 0;
    this._animating = false;

    // Click → open dashboard
    this.canvas.addEventListener('click', () => {
      window.open('http://localhost:3000', '_blank');
    });

    this.startRender();
  }

  onAvatarState(msg) {
    this.mood = msg.mood;
    if (msg.geometry) {
      this.targetGeo = { ...msg.geometry };
    }
    // Show canvas when non-idle
    if (this.mood !== 'idle') {
      this.canvas.classList.add('visible');
    }
  }

  startRender() {
    const loop = (ts) => {
      const delta = ts - this.lastFrame;
      if (delta >= 50) { // throttle to ~20 FPS
        this._lerpAll(0.15);
        this.render();
        this.lastFrame = ts;
      }
      requestAnimationFrame(loop);
    };
    requestAnimationFrame(loop);
  }

  _lerp(a, b, t) { return a + (b - a) * t; }

  _lerpAll(t) {
    const cur = this.currentGeo;
    const tgt = this.targetGeo;
    cur.head_tilt_x = this._lerp(cur.head_tilt_x, tgt.head_tilt_x, t);
    cur.head_tilt_y = this._lerp(cur.head_tilt_y, tgt.head_tilt_y, t);
    cur.head_tilt_z = this._lerp(cur.head_tilt_z, tgt.head_tilt_z, t);
    cur.eye_gaze_x = this._lerp(cur.eye_gaze_x, tgt.eye_gaze_x, t);
    cur.eye_gaze_y = this._lerp(cur.eye_gaze_y, tgt.eye_gaze_y, t);
    cur.left_eyebrow_raise = this._lerp(cur.left_eyebrow_raise, tgt.left_eyebrow_raise, t);
    cur.right_eyebrow_raise = this._lerp(cur.right_eyebrow_raise, tgt.right_eyebrow_raise, t);
    cur.mouth_shape = tgt.mouth_shape;
    cur.blink = tgt.blink;
  }

  render() {
    const ctx = this.ctx;
    const W = this.canvas.width, H = this.canvas.height;
    ctx.clearRect(0, 0, W, H);

    const cx = W / 2, cy = H / 2;
    const R = 80;
    const geo = this.currentGeo;

    ctx.save();
    ctx.translate(cx, cy);
    ctx.rotate(geo.head_tilt_z * 10 * Math.PI / 180);
    ctx.translate(-cx, -cy);

    // Head circle with gradient
    const grad = ctx.createRadialGradient(cx, cy - 10, R * 0.5, cx, cy, R);
    grad.addColorStop(0, '#FFDAB9');
    grad.addColorStop(1, '#D4956A');
    ctx.fillStyle = grad;
    ctx.strokeStyle = '#2a1a0a';
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.arc(cx, cy, R, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();

    // Eyes
    const eyeY = cy - 10;
    const eyeSpacing = 22;
    const eyeW = 16, eyeH = 11;

    for (const eyeX of [cx - eyeSpacing, cx + eyeSpacing]) {
      // White sclera
      ctx.fillStyle = '#FFFFFF';
      ctx.strokeStyle = '#CCCCCC';
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.ellipse(eyeX, eyeY, eyeW / 2, eyeH / 2, 0, 0, Math.PI * 2);
      ctx.fill();
      ctx.stroke();

      // Pupil offset by gaze
      const gx = geo.eye_gaze_x * 5;
      const gy = geo.eye_gaze_y * 5;
      ctx.fillStyle = '#1a0a00';
      ctx.beginPath();
      ctx.arc(eyeX + gx, eyeY + gy, 5, 0, Math.PI * 2);
      ctx.fill();

      // Highlight
      ctx.fillStyle = '#FFFFFF';
      ctx.beginPath();
      ctx.arc(eyeX + gx + 1, eyeY + gy - 1, 2, 0, Math.PI * 2);
      ctx.fill();
    }

    // Eyelids (blink)
    if (geo.blink) {
      ctx.fillStyle = '#D4956A';
      ctx.strokeStyle = '#D4956A';
      ctx.lineWidth = 2;
      for (const eyeX of [cx - eyeSpacing, cx + eyeSpacing]) {
        ctx.beginPath();
        ctx.ellipse(eyeX, eyeY, eyeW / 2, eyeH / 2, 0, 0, Math.PI * 2);
        ctx.fill();
      }
    }

    // Eyebrows
    ctx.strokeStyle = '#3a1a00';
    ctx.lineWidth = 2.5;
    ctx.lineCap = 'round';
    ctx.beginPath();
    const browY = eyeY - 14;
    for (const [eyeX, raise] of [[cx - eyeSpacing, geo.left_eyebrow_raise], [cx + eyeSpacing, geo.right_eyebrow_raise]]) {
      const yOffset = raise * 8;
      ctx.beginPath();
      ctx.arc(eyeX, browY - yOffset, 10, Math.PI * 1.2, Math.PI * 1.8);
      ctx.stroke();
    }

    // Mouth
    const mouthY = cy + 25;
    const mouthW = 20;
    ctx.strokeStyle = '#8B4513';
    ctx.fillStyle = '#8B4513';
    ctx.lineWidth = 2;

    const shape = geo.mouth_shape;
    if (shape === 'SMILE') {
      ctx.beginPath();
      ctx.moveTo(cx - mouthW, mouthY);
      ctx.quadraticCurveTo(cx, mouthY + 12, cx + mouthW, mouthY);
      ctx.stroke();
    } else if (shape === 'OPEN') {
      ctx.beginPath();
      ctx.ellipse(cx, mouthY, mouthW / 2, 5, 0, 0, Math.PI * 2);
      ctx.fill();
    } else if (shape === 'OOH') {
      ctx.beginPath();
      ctx.arc(cx, mouthY, 6, 0, Math.PI * 2);
      ctx.fill();
    } else if (shape === 'FROWN') {
      ctx.beginPath();
      ctx.moveTo(cx - mouthW, mouthY + 5);
      ctx.quadraticCurveTo(cx, mouthY - 5, cx + mouthW, mouthY + 5);
      ctx.stroke();
    } else { // NEUTRAL
      ctx.beginPath();
      ctx.moveTo(cx - mouthW / 2, mouthY);
      ctx.lineTo(cx + mouthW / 2, mouthY);
      ctx.stroke();
    }

    ctx.restore();
  }
}

// ── Init ─────────────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
  setupNav();
  connectAvatarWS();
  // Poll status every 5s
  setInterval(pollStatus, 5000);
  pollStatus();
});