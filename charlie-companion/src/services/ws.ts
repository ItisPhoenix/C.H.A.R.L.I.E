const WS_URL = 'ws://127.0.0.1:8090/ws/events';

type StatusCallback = (state: number) => void;
type ToastCallback = (msg: string) => void;

export class CompanionWS {
  private ws: WebSocket | null = null;
  private reconnectDelay = 1000;
  private onStatus: StatusCallback;
  private onToast: ToastCallback;

  constructor(onStatus: StatusCallback, onToast: ToastCallback) {
    this.onStatus = onStatus;
    this.onToast = onToast;
  }

  connect(token?: string) {
    let url = WS_URL;
    if (token) url += `?token=${encodeURIComponent(token)}`;

    try {
      this.ws = new WebSocket(url);
    } catch {
      this.scheduleReconnect();
      return;
    }

    this.ws.onopen = () => {
      console.log('[CompanionWS] connected');
      this.reconnectDelay = 1000;
    };

    this.ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        this.handleMessage(msg);
      } catch { /* ignore malformed */ }
    };

    this.ws.onclose = () => {
      console.log('[CompanionWS] disconnected');
      this.scheduleReconnect();
    };

    this.ws.onerror = () => {
      this.ws?.close();
    };
  }

  private handleMessage(msg: any) {
    const type = msg.type || '';
    const data = msg.data || {};

    switch (type) {
      case 'VOICE_ACTIVITY': {
        const state = data.state || '';
        if (state === 'listening') this.onStatus(1);
        else if (state === 'speaking') this.onStatus(3);
        else if (state === 'idle') this.onStatus(0);
        break;
      }
      case 'PHASE': {
        const phase = (data.content || '').toLowerCase();
        if (phase === 'thinking' || phase === 'planning') {
          this.onStatus(2);
          this.onToast(`🧠 ${data.content}`);
        }
        break;
      }
      case 'THINKING_STATUS':
      case 'ORCHESTRATOR_UPDATE': {
        const content = data.content || data.step || '';
        if (content) this.onToast(`⚡ ${content}`);
        break;
      }
    }
  }

  private scheduleReconnect() {
    setTimeout(() => {
      this.reconnectDelay = Math.min(this.reconnectDelay * 2, 30000);
      this.connect();
    }, this.reconnectDelay);
  }

  send(data: object) {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(data));
    }
  }

  disconnect() {
    this.ws?.close();
    this.ws = null;
  }
}
