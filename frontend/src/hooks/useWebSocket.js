import { useEffect, useRef, useCallback, useState } from 'react';

const RECONNECT_DELAY_INIT = 1000;
const RECONNECT_DELAY_MAX = 5000;

export function useWebSocket(url) {
  const wsRef = useRef(null);
  const handlersRef = useRef([]);
  const reconnectDelay = useRef(RECONNECT_DELAY_INIT);
  const reconnectTimer = useRef(null);
  const [readyState, setReadyState] = useState(WebSocket.CLOSED);

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return;

    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => {
      setReadyState(WebSocket.OPEN);
      reconnectDelay.current = RECONNECT_DELAY_INIT;
    };

    ws.onmessage = (event) => {
      let data;
      try {
        data = JSON.parse(event.data);
      } catch (err) {
        console.error('Failed to parse WebSocket message:', err);
        return;
      }
      handlersRef.current.forEach((h) => {
        try {
          h(data);
        } catch (err) {
          console.error('Error in WebSocket handler:', err);
        }
      });
    };

    ws.onclose = () => {
      setReadyState(WebSocket.CLOSED);
      reconnectTimer.current = setTimeout(() => {
        reconnectDelay.current = Math.min(
          reconnectDelay.current * 2,
          RECONNECT_DELAY_MAX,
        );
        connect();
      }, reconnectDelay.current);
    };

    ws.onerror = () => {
      ws.close();
    };
  }, [url]);

  useEffect(() => {
    connect();
    return () => {
      clearTimeout(reconnectTimer.current);
      if (wsRef.current) {
        wsRef.current.onclose = null; // prevent reconnect on intentional close
        wsRef.current.close();
      }
    };
  }, [connect]);

  const send = useCallback((data) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(data));
    }
  }, []);

  const onMessage = useCallback((handler) => {
    handlersRef.current.push(handler);
    return () => {
      handlersRef.current = handlersRef.current.filter((h) => h !== handler);
    };
  }, []);

  return { send, onMessage, readyState };
}
