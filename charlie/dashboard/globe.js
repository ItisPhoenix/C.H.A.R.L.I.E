/**
 * charlie/dashboard/globe.js
 *
 * Globe.gl integration for CHARLIE dashboard.
 * 8 data layers, 4 view modes, tab-focused hybrid refresh.
 */

const CONTROLLER_URL = 'http://localhost:8090';
const WS_URL = 'ws://localhost:8090/ws/events';
const REFRESH_INTERVAL = 5 * 60 * 1000; // 5 min

// ── 20 Major Cities for weather layer ─────────────────────────────────────────
const MAJOR_CITIES = [
  { name: 'New York', lat: 40.7128, lng: -74.0060 },
  { name: 'London', lat: 51.5074, lng: -0.1278 },
  { name: 'Tokyo', lat: 35.6762, lng: 139.6503 },
  { name: 'Sydney', lat: -33.8688, lng: 151.2093 },
  { name: 'Moscow', lat: 55.7558, lng: 37.6173 },
  { name: 'Dubai', lat: 25.2048, lng: 55.2708 },
  { name: 'Singapore', lat: 1.3521, lng: 103.8198 },
  { name: 'Mumbai', lat: 19.0760, lng: 72.8777 },
  { name: 'Cairo', lat: 30.0444, lng: 31.2357 },
  { name: 'São Paulo', lat: -23.5505, lng: -46.6333 },
  { name: 'Beijing', lat: 39.9042, lng: 116.4074 },
  { name: 'Paris', lat: 48.8566, lng: 2.3522 },
  { name: 'Berlin', lat: 52.5200, lng: 13.4050 },
  { name: 'Los Angeles', lat: 34.0522, lng: -118.2437 },
  { name: 'Toronto', lat: 43.6532, lng: -79.3832 },
  { name: 'Mexico City', lat: 19.4326, lng: -99.1332 },
  { name: 'Seoul', lat: 37.5665, lng: 126.9780 },
  { name: 'Lagos', lat: 6.5244, lng: 3.3792 },
  { name: 'Buenos Aires', lat: -34.6037, lng: -58.3816 },
  { name: 'Mumbai', lat: 19.0760, lng: 72.8777 },
];

// ── Layer color map ────────────────────────────────────────────────────────────
const LAYER_COLORS = {
  earthquake: '#ff3c3c',
  nasa: '#ffdc6a',
  news: '#ff963c',
  weather: '#6a8aff',
  calendar: '#00d4ff',
  memory: '#b45cff',
  workspace: '#3cdc78',
  user: '#3cdc78',
};

// ── City geocode cache ─────────────────────────────────────────────────────────
const CITY_COORDS = {
  'tokyo': [35.6762, 139.6503], 'new york': [40.7128, -74.0060], 'london': [51.5074, -0.1278],
  'sydney': [-33.8688, 151.2093], 'moscow': [55.7558, 37.6173], 'dubai': [25.2048, 55.2708],
  'singapore': [1.3521, 103.8198], 'mumbai': [19.0760, 72.8777], 'cairo': [30.0444, 31.2357],
  'sao paulo': [-23.5505, -46.6333], 'buenos aires': [-34.6037, -58.3816], 'beijing': [39.9042, 116.4074],
  'paris': [48.8566, 2.3522], 'berlin': [52.52, 13.405], 'los angeles': [34.0522, -118.2437],
  'toronto': [43.6532, -79.3832], 'mexico city': [19.4326, -99.1332], 'seoul': [37.5665, 126.9780],
  'lagos': [6.5244, 3.3792], 'chicago': [41.8781, -87.6298], 'hong kong': [22.3193, 114.1694],
  'san francisco': [37.7749, -122.4194], 'shanghai': [31.2304, 121.4737], 'delhi': [28.7041, 77.1025],
  'bangkok': [13.7563, 100.5018], 'jakarta': [-6.2088, 106.8456], 'manila': [14.5995, 120.9842],
  'nairobi': [-1.2921, 36.8219], 'johannesburg': [-26.2041, 28.0473], 'istanbul': [41.0082, 28.9784],
};

function geocodeCity(cityStr) {
  if (!cityStr) return null;
  const normalized = cityStr.toLowerCase().trim();
  if (CITY_COORDS[normalized]) return CITY_COORDS[normalized];
  // Try partial match
  for (const [name, coords] of Object.entries(CITY_COORDS)) {
    if (normalized.includes(name) || name.includes(normalized)) return coords;
  }
  return null;
}

// ── State ─────────────────────────────────────────────────────────────────────
let globeInstance = null;
let ws = null;
let wsReconnectDelay = 1000;
let refreshTimers = {};
let layerData = {
  earthquakes: [],
  nasa: [],
  news: [],
  weather: [],
  calendar: [],
  memory: [],
  workspace: [],
  user: null,
};
let activeLayers = new Set(['earthquake', 'nasa', 'news', 'weather', 'calendar', 'memory', 'workspace', 'user']);
let activeView = 'world';
let autoRotate = true;

// ── GlobeApp ──────────────────────────────────────────────────────────────────
class GlobeApp {
  constructor(container) {
    this.container = container;
  }

  async init() {
    this.container.innerHTML = '<div class="loading">Loading Globe...</div>';
    try {
      await this._initGlobe();
      await this._fetchAllData();
      this._connectWs();
      this._startTimers();
      this._bindToolbar();
      document.getElementById('globe-container').innerHTML = '';
    } catch (err) {
      console.error('GlobeApp init failed:', err);
      this.container.innerHTML = `<div class="loading">Failed to load globe: ${err.message}</div>`;
    }
  }

  destroy() {
    this._stopTimers();
    this._disconnectWs();
    if (globeInstance) {
      try { globeInstance._destructor && globeInstance._destructor(); } catch (_) {}
      globeInstance = null;
    }
  }

  // ── Globe.gl init ──────────────────────────────────────────────────────────
  async _initGlobe() {
    const Globe = await import('https://unpkg.com/three-globe@2.25/globe.gl.js');

    globeInstance = Globe();

    globeInstance
      .globeImageUrl('//unpkg.com/three-globe/example/img/earth-night.jpg')
      .backgroundImageUrl('//unpkg.com/three-globe/example/img/night-sky.png')
      .pointLat('lat')
      .pointLng('lng')
      .pointAltitude(0.008)
      .pointRadius('size')
      .pointColor(() => '#00d4ff')
      .pointLabel(d => `<strong>${d.label || d.name || 'Point'}</strong><br>${d.info || ''}`)
      .arcLat('lat')
      .arcLng('lng')
      .arcColor(() => 'rgba(0,212,255,0.4)')
      .arcAltitude(0.3)
      .arcStroke(0.5)
      .hexBinPointsLat('lat')
      .hexBinLng('lng')
      .hexBinAlt(d => d.count * 0.0005)
      .hexBinColor(d => `rgba(106,138,255,${Math.min(0.8, d.count * 0.15)})`)
      .hexBinRadius(1)
      .onPointClick(d => this._showDetail(d))
      .onArcClick(d => this._showDetail(d));

    const container = document.getElementById('globe-container');
    globeInstance(document.getElementById('globe-container'));

    if (autoRotate) {
      globeInstance.controls().autoRotate = true;
      globeInstance.controls().autoRotateSpeed = 0.3;
    }

    // Resize handler
    const ro = new ResizeObserver(() => {
      const w = container.clientWidth;
      const h = container.clientHeight;
      if (w && h) globeInstance.width(w).height(h)();
    });
    ro.observe(container);
    this._resizeObserver = ro;
  }

  // ── Data fetching ──────────────────────────────────────────────────────────
  async _fetchAllData() {
    await Promise.allSettled([
      this._fetchEarthquakes(),
      this._fetchNasa(),
      this._fetchNews(),
      this._fetchWeather(),
      this._fetchLocalData(),
    ]);
    this._renderLayers();
  }

  async _fetchEarthquakes() {
    try {
      const resp = await fetch(
        'https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_day.geojson'
      );
      if (!resp.ok) throw new Error(resp.statusText);
      const data = await resp.json();
      layerData.earthquakes = (data.features || []).map(f => {
        const [lng, lat, depth] = f.geometry.coordinates;
        return {
          lat, lng,
          size: Math.max(0.3, Math.min(1.5, f.properties.mag / 3)),
          label: f.properties.place,
          info: `M${f.properties.mag} | Depth: ${depth.toFixed(1)}km<br>${new Date(f.properties.time).toLocaleString()}`,
          mag: f.properties.mag,
          depth,
          url: f.properties.url,
        };
      });
    } catch (err) {
      console.warn('Earthquake fetch failed:', err.message);
    }
  }

  async _fetchNasa() {
    try {
      const resp = await fetch('https://eonet.gsfc.nasa.gov/api/v3/events?status=open&days=7');
      if (!resp.ok) throw new Error(resp.statusText);
      const data = await resp.json();
      layerData.nasa = (data.events || []).map(e => {
        const cat = (e.categories[0] || {}).title || '';
        const loc = e.geometry[0];
        if (!loc) return null;
        return {
          lat: loc.coordinates[1],
          lng: loc.coordinates[0],
          size: 0.5,
          label: e.title,
          info: `${cat}<br>${new Date(loc.date).toLocaleDateString()}<br><a href="${e.sources?.[0]?.url || '#'}" target="_blank">Source</a>`,
          category: cat,
        };
      }).filter(Boolean);
    } catch (err) {
      console.warn('NASA EONET fetch failed:', err.message);
    }
  }

  async _fetchNews() {
    // Primary: NewsAPI (free tier, requires key for non-BBC) — show at origin
    // Fallback: BBC World News RSS — geolocated by country from feed
    try {
      const rssResp = await fetch(
        "https://feeds.bbcidc.com/bbc-world-news/rss.xml"
      );
      if (rssResp.ok) {
        const xmlText = await rssResp.text();
        const parser = new DOMParser();
        const xml = parser.parseFromString(xmlText, "text/xml");
        const items = Array.from(xml.querySelectorAll("item")).slice(0, 30);
        layerData.news = items.map((item, i) => {
          const title = item.querySelector("title")?.textContent || "";
          const desc = item.querySelector("description")?.textContent || "";
          const geo = this._parseGeoFromText(title + " " + desc);
          return {
            lat: geo.lat, lng: geo.lng, label: title.slice(0, 70),
            url: item.querySelector("link")?.textContent || "",
            article: { title, description: desc },
            id: `news-${i}`
          };
        }).filter(n => n.lat !== null);
        return;
      }
    } catch { /* fall through to empty */ }
    layerData.news = [];
  }

  _parseGeoFromText(text) {
    // Detect country/region mentions for geolocation
    const map = {
      "united states": { lat: 38.9, lng: -77.0 }, "usa": { lat: 38.9, lng: -77.0 },
      "uk": { lat: 51.5, lng: -0.1 }, "united kingdom": { lat: 51.5, lng: -0.1 },
      "europe": { lat: 48.8, lng: 9.2 }, "germany": { lat: 52.5, lng: 13.4 },
      "france": { lat: 48.8, lng: 2.3 }, "china": { lat: 39.9, lng: 116.4 },
      "india": { lat: 20.6, lng: 78.9 }, "japan": { lat: 35.7, lng: 139.7 },
      "australia": { lat: -25.3, lng: 133.9 }, "brazil": { lat: -15.8, lng: -47.9 },
      "russia": { lat: 55.7, lng: 37.6 }, "middle east": { lat: 29.3, lng: 47.5 },
      "africa": { lat: -1.3, lng: 15.6 }, "latin america": { lat: -15.6, lng: -66.7 },
      "middle east": { lat: 29.3, lng: 47.5 }, "asia": { lat: 34.0, lng: 103.0 },
      "arctic": { lat: 80.0, lng: 0.0 }, "antarctic": { lat: -80.0, lng: 0.0 },
      "pacific": { lat: 0.0, lng: -160.0 }, "atlantic": { lat: 20.0, lng: -40.0 },
      "north korea": { lat: 39.0, lng: 125.8 }, "south korea": { lat: 37.6, lng: 127.0 },
      "ukraine": { lat: 50.4, lng: 30.5 }, "israel": { lat: 31.8, lng: 35.2 },
      "iran": { lat: 32.4, lng: 53.7 }, "turkey": { lat: 39.9, lng: 32.8 },
      "greece": { lat: 37.9, lng: 23.7 }, "spain": { lat: 40.4, lng: -3.7 },
      "italy": { lat: 41.9, lng: 12.6 }, "canada": { lat: 45.4, lng: -75.7 },
      "mexico": { lat: 19.4, lng: -99.1 }, "indonesia": { lat: -6.2, lng: 106.8 },
    };
    const lower = text.toLowerCase();
    for (const [kw, coords] of Object.entries(map)) {
      if (lower.includes(kw)) return coords;
    }
    return { lat: null, lng: null };
  }

  async _fetchWeather() {
    try {
      const cityData = await Promise.allSettled(
        MAJOR_CITIES.map(async city => {
          const resp = await fetch(
            `https://api.open-meteo.com/v1/forecast?latitude=${city.lat}&longitude=${city.lng}&current_weather=true&temperature_unit=celsius`
          );
          if (!resp.ok) return null;
          const json = await resp.json();
          const temp = json.current_weather?.temperature ?? 20;
          return { ...city, temp };
        })
      );
      layerData.weather = cityData
        .filter(r => r.status === 'fulfilled' && r.value)
        .map(r => r.value);
    } catch (err) {
      console.warn('Weather fetch failed:', err.message);
    }
  }

  async _fetchLocalData() {
    try {
      const resp = await fetch(`${CONTROLLER_URL}/api/globe/data`);
      if (!resp.ok) throw new Error(resp.statusText);
      const data = await resp.json();

      if (data.calendar) {
        layerData.calendar = (data.calendar).filter(e => e.lat && e.lng).map(e => ({
          lat: e.lat, lng: e.lng,
          size: 0.4,
          label: e.title,
          info: `${e.start ? new Date(e.start).toLocaleString() : ''}<br>${e.location || ''}`,
        }));
      }

      if (data.memory) {
        layerData.memory = (data.memory).filter(e => e.lat && e.lng).map(e => ({
          lat: e.lat, lng: e.lng,
          size: 0.3,
          label: e.content?.substring(0, 60),
          info: e.content || '',
        }));
      }

      if (data.workspace) {
        layerData.workspace = (data.workspace).filter(e => e.lat && e.lng).map(e => ({
          lat: e.lat, lng: e.lng,
          size: 0.35,
          label: e.app,
          info: e.detail || '',
        }));
      }

      if (data.user_position) {
        layerData.user = {
          lat: data.user_position.lat,
          lng: data.user_position.lng,
          size: 0.6,
          label: data.user_position.label || 'You',
          info: 'Your location',
        };
      }
    } catch (err) {
      console.warn('Local globe data fetch failed:', err.message);
    }
  }

  async _refresh() {
    await this._fetchAllData();
  }

  // ── WebSocket ─────────────────────────────────────────────────────────────
  _connectWs() {
    try {
      ws = new WebSocket(WS_URL);
      ws.onmessage = (evt) => {
        try {
          const msg = JSON.parse(evt.data);
          if (msg.type === 'globe_refresh' || msg.type === 'globe_data') {
            this._applyWsData(msg.data);
          }
        } catch (_) {}
      };
      ws.onclose = () => {
        wsReconnectDelay = Math.min(wsReconnectDelay * 1.5, 30000);
        setTimeout(() => this._connectWs(), wsReconnectDelay);
      };
      ws.onerror = () => ws.close();
    } catch (err) {
      console.warn('WS connect failed:', err.message);
    }
  }

  _disconnectWs() {
    if (ws) { ws.close(); ws = null; }
  }

  _applyWsData(data) {
    if (!data) return;
    if (data.calendar) {
      layerData.calendar = (data.calendar).filter(e => e.lat && e.lng).map(e => ({
        lat: e.lat, lng: e.lng, size: 0.4,
        label: e.title,
        info: `${e.start ? new Date(e.start).toLocaleString() : ''}<br>${e.location || ''}`,
      }));
    }
    if (data.memory) {
      layerData.memory = (data.memory).filter(e => e.lat && e.lng).map(e => ({
        lat: e.lat, lng: e.lng, size: 0.3,
        label: e.content?.substring(0, 60),
        info: e.content || '',
      }));
    }
    if (data.workspace) {
      layerData.workspace = (data.workspace).filter(e => e.lat && e.lng).map(e => ({
        lat: e.lat, lng: e.lng, size: 0.35,
        label: e.app, info: e.detail || '',
      }));
    }
    if (data.user_position) {
      layerData.user = {
        lat: data.user_position.lat, lng: data.user_position.lng,
        size: 0.6, label: data.user_position.label || 'You', info: 'Your location',
      };
    }
    this._renderLayers();
  }

  // ── Render ─────────────────────────────────────────────────────────────────
  _renderLayers() {
    if (!globeInstance) return;

    // Build points data per view mode
    const worldLayers = ['earthquake', 'nasa', 'news', 'weather'];
    const knowledgeLayers = ['memory'];
    const workspaceLayers = ['workspace', 'user'];

    let points = [];

    if (activeView === 'world' || activeView === 'combined') {
      for (const l of worldLayers) {
        if (activeLayers.has(l) && layerData[l]?.length) {
          points = points.concat(layerData[l].map(d => ({ ...d, _layer: l })));
        }
      }
    }
    if (activeView === 'knowledge' || activeView === 'combined') {
      for (const l of knowledgeLayers) {
        if (activeLayers.has(l) && layerData[l]?.length) {
          points = points.concat(layerData[l].map(d => ({ ...d, _layer: l })));
        }
      }
    }
    if (activeView === 'workspace' || activeView === 'combined') {
      for (const l of workspaceLayers) {
        if (activeLayers.has(l) && layerData[l]?.length) {
          points = points.concat(layerData[l].map(d => ({ ...d, _layer: l })));
        }
      }
    }

    // Weather as hexbins if visible
    if (activeLayers.has('weather') && layerData.weather?.length) {
      const hexData = layerData.weather.map(c => ({
        lat: c.lat, lng: c.lng, count: Math.abs(c.temp) / 10 + 0.5,
      }));
      globeInstance.hexBinPointsData(hexData);
    } else {
      globeInstance.hexBinPointsData([]);
    }

    // Points
    globeInstance.pointsData(points);
    globeInstance.pointColor(d => LAYER_COLORS[d._layer] || '#00d4ff');
    globeInstance.pointRadius(0.15);

    // User position as special arc
    if (activeLayers.has('user') && layerData.user) {
      globeInstance.arcsData([{
        startLat: layerData.user.lat, startLng: layerData.user.lng,
        endLat: 0, endLng: 0,
        color: 'rgba(60,220,120,0.3)',
      }]);
    } else {
      globeInstance.arcsData([]);
    }
  }

  // ── Timers ────────────────────────────────────────────────────────────────
  _startTimers() {
    this._stopTimers();
    const tick = () => {
      if (!document.hidden) {
        this._fetchAllData().catch(console.warn);
      }
    };
    refreshTimers.main = setInterval(tick, REFRESH_INTERVAL);
  }

  _stopTimers() {
    for (const t of Object.values(refreshTimers)) clearInterval(t);
    refreshTimers = {};
  }

  // ── Toolbar ───────────────────────────────────────────────────────────────
  _bindToolbar() {
    // View mode buttons
    document.querySelectorAll('.view-mode-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        document.querySelectorAll('.view-mode-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        activeView = btn.dataset.view;
        this._renderLayers();
      });
    });

    // Layer toggles
    document.querySelectorAll('.layer-toggle input').forEach(cb => {
      const layer = cb.id.replace('layer-', '');
      cb.addEventListener('change', () => {
        if (cb.checked) activeLayers.add(layer);
        else activeLayers.delete(layer);
        this._renderLayers();
      });
    });

    // Refresh button
    const refreshBtn = document.getElementById('globe-refresh');
    if (refreshBtn) {
      refreshBtn.addEventListener('click', () => {
        refreshBtn.textContent = '↻ Refreshing...';
        this._refresh().finally(() => {
          setTimeout(() => { refreshBtn.textContent = '↻ Refresh'; }, 800);
        });
      });
    }

    // Auto-rotate
    const autoRotateBtn = document.getElementById('globe-auto-rotate');
    if (autoRotateBtn) {
      autoRotateBtn.addEventListener('click', () => {
        autoRotate = !autoRotate;
        if (globeInstance) {
          globeInstance.controls().autoRotate = autoRotate;
        }
        autoRotateBtn.classList.toggle('active', autoRotate);
      });
    }

    // Close detail panel
    const panel = document.getElementById('globe-detail-panel');
    const closeBtn = panel?.querySelector('.close-btn');
    if (closeBtn) {
      closeBtn.addEventListener('click', () => {
        panel.style.display = 'none';
      });
    }
  }

  // ── Detail panel ──────────────────────────────────────────────────────────
  _showDetail(d) {
    if (!d) return;
    const panel = document.getElementById('globe-detail-panel');
    if (!panel) return;

    const layer = d._layer || d.layer || 'unknown';
    const color = LAYER_COLORS[layer] || '#00d4ff';
    const badge = `<span class="globe-badge globe-badge-${layer}">${layer}</span>`;

    let content = `<button class="close-btn">×</button>`;
    content += `<h4>${badge} ${d.label || 'Details'}</h4>`;

    if (d.info) {
      const infoParts = d.info.split('<br>');
      for (const part of infoParts) {
        const trimmed = part.trim();
        if (!trimmed) continue;
        content += `<div class="detail-row"><div class="detail-value" style="max-width:none">${trimmed}</div></div>`;
      }
    }

    if (d.mag !== undefined) {
      content += `<div class="detail-row"><div class="detail-label">Magnitude</div><div class="detail-value">M${d.mag}</div></div>`;
    }
    if (d.depth !== undefined) {
      content += `<div class="detail-row"><div class="detail-label">Depth</div><div class="detail-value">${d.depth.toFixed(1)} km</div></div>`;
    }
    if (d.lat !== undefined && d.lng !== undefined) {
      content += `<div class="detail-row"><div class="detail-label">Location</div><div class="detail-value">${d.lat.toFixed(4)}, ${d.lng.toFixed(4)}</div></div>`;
    }
    if (d.url) {
      const label = layer === 'news' ? 'Read article →' : 'View on USGS →';
      content += `<div class="detail-row" style="border:none;margin-top:8px"><a href="${d.url}" target="_blank" style="color:#00d4ff;font-size:12px">${label}</a></div>`;
    }

    panel.innerHTML = content;
    panel.style.display = 'block';

    // Rebind close
    panel.querySelector('.close-btn')?.addEventListener('click', () => {
      panel.style.display = 'none';
    });
  }
}

window.GlobeApp = GlobeApp;