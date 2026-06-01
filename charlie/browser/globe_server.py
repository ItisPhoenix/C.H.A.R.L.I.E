"""
C.H.A.R.L.I.E. — Globe Server
HTTP server on port 8089 that serves a 3D earth visualization with real data.
"""

import json
import http.server
import socketserver
import urllib.request
import time
import os

PORT = 8089
API_BASE = "http://127.0.0.1:8090"

GLOBE_HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>CHARLIE Globe</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { background: #0a0a0c; overflow: hidden; font-family: 'Segoe UI', system-ui, sans-serif; color: #e4e4e7; }
canvas { display: block; }
#panel { position: absolute; top: 20px; right: 20px; width: 280px; background: rgba(10,10,12,0.9); border: 1px solid rgba(255,255,255,0.06); border-radius: 12px; padding: 16px; backdrop-filter: blur(12px); }
#panel h3 { font-size: 14px; color: #88ccff; margin-bottom: 12px; font-weight: 600; }
.metric { display: flex; justify-content: space-between; padding: 6px 0; border-bottom: 1px solid rgba(255,255,255,0.04); font-size: 12px; }
.metric .label { color: #71717a; }
.metric .value { color: #e4e4e7; font-family: monospace; }
#info { position: absolute; bottom: 20px; left: 20px; color: #71717a; font-size: 11px; }
.status-dot { width: 6px; height: 6px; border-radius: 50%; display: inline-block; margin-right: 6px; }
.online { background: #22c55e; box-shadow: 0 0 6px rgba(34,197,94,0.4); }
.offline { background: #71717a; }
.event { padding: 8px 0; border-bottom: 1px solid rgba(255,255,255,0.04); }
.event-title { font-size: 12px; color: #e4e4e7; }
.event-time { font-size: 10px; color: #71717a; margin-top: 2px; }
</style>
</head>
<body>
<canvas id="globe"></canvas>
<div id="panel">
  <h3>CHARLIE Globe</h3>
  <div id="metrics">Loading...</div>
  <h3 style="margin-top:16px">Events</h3>
  <div id="events">Loading...</div>
</div>
<div id="info">Drag to rotate &bull; Auto-refreshes every 30s</div>
<script>
const canvas = document.getElementById('globe');
const ctx = canvas.getContext('2d');
let w, h, rot = 0, autoRotate = true;
let mouseX = 0, mouseDown = false;

function resize() { w = canvas.width = window.innerWidth; h = canvas.height = window.innerHeight; }
resize();
window.addEventListener('resize', resize);

canvas.addEventListener('mousedown', (e) => { mouseDown = true; mouseX = e.clientX; autoRotate = false; });
canvas.addEventListener('mousemove', (e) => { if (mouseDown) { rot += (e.clientX - mouseX) * 0.3; mouseX = e.clientX; } });
canvas.addEventListener('mouseup', () => { mouseDown = false; setTimeout(() => autoRotate = true, 3000); });

const cities = [
  {lat:51.5,lon:-0.1,name:'London'},{lat:40.7,lon:-74,name:'New York'},
  {lat:35.7,lon:139.7,name:'Tokyo'},{lat:-33.9,lon:151.2,name:'Sydney'},
  {lat:48.9,lon:2.3,name:'Paris'},{lat:55.8,lon:37.6,name:'Moscow'},
  {lat:39.9,lon:116.4,name:'Beijing'},{lat:-23.5,lon:-46.6,name:'Sao Paulo'},
];

let calendarEvents = [], memoryNodes = [], subsystems = {};

async function fetchData() {
  try {
    const [globe, status] = await Promise.all([
      fetch('/api/globe/data').then(r=>r.json()).catch(()=>({})),
      fetch('http://127.0.0.1:8090/api/status').then(r=>r.json()).catch(()=>({})),
    ]);
    calendarEvents = globe.calendar || [];
    memoryNodes = globe.memory || [];
    subsystems = status.subsystems || {};
    renderPanel();
  } catch(e) { console.error('Fetch failed:', e); }
}

function renderPanel() {
  // Metrics
  const online = Object.values(subsystems).filter(s=>s.status==='running').length;
  const total = Object.keys(subsystems).length;
  let html = `<div class="metric"><span class="label">Subsystems</span><span class="value"><span class="status-dot ${online>0?'online':'offline'}"></span>${online}/${total}</span></div>`;
  html += `<div class="metric"><span class="label">Calendar</span><span class="value">${calendarEvents.length} events</span></div>`;
  html += `<div class="metric"><span class="label">Memory</span><span class="value">${memoryNodes.length} nodes</span></div>`;
  document.getElementById('metrics').innerHTML = html;

  // Events
  let evHtml = '';
  if (calendarEvents.length > 0) {
    calendarEvents.slice(0,5).forEach(e => {
      evHtml += `<div class="event"><div class="event-title">${e.title||'Event'}</div><div class="event-time">${e.time||''}</div></div>`;
    });
  } else {
    evHtml = '<div style="color:#71717a;font-size:12px">No upcoming events</div>';
  }
  document.getElementById('events').innerHTML = evHtml;
}

function drawGlobe() {
  ctx.clearRect(0, 0, w, h);
  const cx = w * 0.6, cy = h / 2;
  const r = Math.min(w, h) * 0.35;

  // Atmosphere glow
  const grad = ctx.createRadialGradient(cx, cy, r * 0.9, cx, cy, r * 1.2);
  grad.addColorStop(0, 'rgba(136,204,255,0.05)');
  grad.addColorStop(1, 'transparent');
  ctx.fillStyle = grad;
  ctx.fillRect(0, 0, w, h);

  // Globe outline
  ctx.beginPath(); ctx.arc(cx, cy, r, 0, Math.PI * 2);
  ctx.strokeStyle = 'rgba(136,204,255,0.2)'; ctx.lineWidth = 1; ctx.stroke();

  // Latitude lines
  for (let lat = -60; lat <= 60; lat += 30) {
    const y = cy + (lat/90)*r, xr = Math.sqrt(Math.max(0, r*r - (y-cy)*(y-cy)));
    if (xr > 0) { ctx.beginPath(); ctx.ellipse(cx, y, xr, xr*0.1, 0, 0, Math.PI*2); ctx.strokeStyle='rgba(136,204,255,0.07)'; ctx.stroke(); }
  }

  // Longitude lines
  for (let lon = 0; lon < 360; lon += 30) {
    const angle = (lon+rot)*Math.PI/180;
    ctx.beginPath(); ctx.ellipse(cx, cy, r*Math.abs(Math.cos(angle)), r, 0, 0, Math.PI*2);
    ctx.strokeStyle='rgba(136,204,255,0.07)'; ctx.stroke();
  }

  // Cities
  cities.forEach(c => {
    const lon = (c.lon+rot)*Math.PI/180, lat = c.lat*Math.PI/180;
    const x = cx + r*Math.cos(lat)*Math.sin(lon), y = cy - r*Math.sin(lat);
    if (Math.cos(lon) > 0) {
      ctx.beginPath(); ctx.arc(x, y, 3, 0, Math.PI*2);
      ctx.fillStyle = 'rgba(136,204,255,0.8)'; ctx.fill();
      ctx.fillStyle = 'rgba(136,204,255,0.5)'; ctx.font = '10px sans-serif'; ctx.fillText(c.name, x+6, y+3);
    }
  });

  // Subsystem indicators along bottom
  const subs = Object.entries(subsystems);
  subs.forEach(([name, s], i) => {
    const x = 20 + i * 90, y = h - 30;
    ctx.beginPath(); ctx.arc(x, y, 4, 0, Math.PI*2);
    ctx.fillStyle = s.status === 'running' ? '#22c55e' : '#71717a'; ctx.fill();
    ctx.fillStyle = '#71717a'; ctx.font = '10px sans-serif'; ctx.fillText(name, x+8, y+3);
  });

  if (autoRotate) rot += 0.15;
  requestAnimationFrame(drawGlobe);
}

fetchData();
setInterval(fetchData, 30000);
drawGlobe();
</script>
</body>
</html>"""


class GlobeHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/api/globe/data':
            self._proxy_json(f"{API_BASE}/api/globe/data")
        elif self.path == '/api/status':
            self._proxy_json(f"{API_BASE}/api/status")
        else:
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(GLOBE_HTML.encode())

    def _proxy_json(self, url):
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = resp.read()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(data)
        except Exception:
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(b'{}')

    def log_message(self, format, *args):
        pass


def start_globe_server():
    """Start the globe server."""
    with socketserver.TCPServer(("", PORT), GlobeHandler) as httpd:
        httpd.serve_forever()


if __name__ == "__main__":
    print(f"Starting Globe server on port {PORT}")
    start_globe_server()
