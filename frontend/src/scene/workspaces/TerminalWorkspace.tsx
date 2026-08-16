import { useEffect, useRef, useState, type ReactElement } from "react";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import type { WorkspaceInstance } from "../../layout/workspaceStore";

interface TerminalInitMessage {
  type: "terminal_init";
  session_id: string;
  pid: number | null;
  shell: string;
  status: string;
  cols: number;
  rows: number;
  scrollback: string;
}

interface TerminalOutputMessage {
  type: "output";
  data: string;
}

interface TerminalExitMessage {
  type: "exit";
  exit_code: number;
}

type TerminalWsMessage =
  | TerminalInitMessage
  | TerminalOutputMessage
  | TerminalExitMessage
  | { type: string; [key: string]: unknown };

export function TerminalWorkspace({ workspace }: { workspace: WorkspaceInstance }): ReactElement {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const terminalRef = useRef<Terminal | null>(null);
  const fitAddonRef = useRef<FitAddon | null>(null);
  const wsRef = useRef<WebSocket | null>(null);

  const [connected, setConnected] = useState(false);
  const [pid, setPid] = useState<number | null>(null);
  const [shellName, setShellName] = useState("powershell.exe");
  const [sessionStatus, setSessionStatus] = useState("connecting");

  const sessionId = workspace.id && workspace.id !== "terminal" ? workspace.id : "primary";

  useEffect(() => {
    if (!containerRef.current) return;

    // Clear previous DOM nodes if re-mounting
    containerRef.current.innerHTML = "";

    const term = new Terminal({
      cursorBlink: true,
      cursorStyle: "block",
      fontSize: 13,
      fontFamily: "JetBrains Mono, Menlo, Monaco, Consolas, monospace",
      theme: {
        background: "#020617", // slate-950
        foreground: "#e2e8f0", // slate-200
        cursor: "#22d3ee", // cyan-400
        cursorAccent: "#020617",
        selectionBackground: "#0891b2", // cyan-600
        black: "#0f172a",
        red: "#ef4444",
        green: "#10b981",
        yellow: "#f59e0b",
        blue: "#06b6d4",
        magenta: "#ec4899",
        cyan: "#22d3ee",
        white: "#f8fafc",
        brightBlack: "#475569",
        brightRed: "#f87171",
        brightGreen: "#34d399",
        brightYellow: "#fbbf24",
        brightBlue: "#38bdf8",
        brightMagenta: "#f472b6",
        brightCyan: "#67e8f9",
        brightWhite: "#ffffff",
      },
      allowTransparency: true,
      scrollback: 5000,
    });

    const fitAddon = new FitAddon();
    term.loadAddon(fitAddon);

    term.open(containerRef.current);
    terminalRef.current = term;
    fitAddonRef.current = fitAddon;

    try {
      fitAddon.fit();
    } catch {
      // Element may not have layout dimensions yet
    }

    // Connect WebSocket
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const url = `${protocol}//${window.location.host}/ws/terminal/${sessionId}`;
    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => {
      setConnected(true);
      setSessionStatus("running");
      try {
        fitAddon.fit();
        ws.send(JSON.stringify({ type: "resize", cols: term.cols, rows: term.rows }));
      } catch {
        // Safe ignore
      }
    };

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data) as TerminalWsMessage;
        if (msg.type === "terminal_init") {
          const init = msg as TerminalInitMessage;
          if (init.pid) setPid(init.pid);
          if (init.shell) setShellName(init.shell);
          if (init.status) setSessionStatus(init.status);
          if (init.scrollback) {
            term.write(init.scrollback);
          }
          try {
            fitAddon.fit();
          } catch {
            // Ignored
          }
        } else if (msg.type === "output") {
          const out = msg as TerminalOutputMessage;
          term.write(out.data);
        } else if (msg.type === "exit") {
          const exitMsg = msg as TerminalExitMessage;
          setSessionStatus("exited");
          term.write(`\r\n\x1b[33m[Process exited with code ${exitMsg.exit_code}]\x1b[0m\r\n`);
        }
      } catch {
        // Raw string fallback
        term.write(event.data);
      }
    };

    ws.onclose = () => {
      setConnected(false);
      setSessionStatus("disconnected");
    };

    ws.onerror = () => {
      setConnected(false);
      setSessionStatus("error");
    };

    // Forward terminal input
    const dataDisposable = term.onData((data) => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "input", data }));
      }
    });

    // Resize observer
    const resizeObserver = new ResizeObserver(() => {
      try {
        fitAddon.fit();
        if (ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: "resize", cols: term.cols, rows: term.rows }));
        }
      } catch {
        // Ignored
      }
    });

    if (containerRef.current) {
      resizeObserver.observe(containerRef.current);
    }

    return () => {
      dataDisposable.dispose();
      resizeObserver.disconnect();
      if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) {
        ws.close();
      }
      term.dispose();
      terminalRef.current = null;
      fitAddonRef.current = null;
      wsRef.current = null;
    };
  }, [sessionId]);

  const handleInterrupt = () => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: "interrupt" }));
    }
  };

  const handleReconnect = () => {
    if (!connected && terminalRef.current) {
      const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
      const url = `${protocol}//${window.location.host}/ws/terminal/${sessionId}`;
      const ws = new WebSocket(url);
      wsRef.current = ws;

      ws.onopen = () => {
        setConnected(true);
        setSessionStatus("running");
        if (fitAddonRef.current && terminalRef.current) {
          fitAddonRef.current.fit();
          ws.send(JSON.stringify({ type: "resize", cols: terminalRef.current.cols, rows: terminalRef.current.rows }));
        }
      };

      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data) as TerminalWsMessage;
          if (msg.type === "terminal_init") {
            const init = msg as TerminalInitMessage;
            if (init.pid) setPid(init.pid);
            if (init.shell) setShellName(init.shell);
            if (init.status) setSessionStatus(init.status);
            if (init.scrollback && terminalRef.current) {
              terminalRef.current.write(init.scrollback);
            }
          } else if (msg.type === "output" && terminalRef.current) {
            const out = msg as TerminalOutputMessage;
            terminalRef.current.write(out.data);
          }
        } catch {
          if (terminalRef.current && typeof event.data === "string") {
            terminalRef.current.write(event.data);
          }
        }
      };

      ws.onclose = () => {
        setConnected(false);
        setSessionStatus("disconnected");
      };
    }
  };

  return (
    <div className="w-full h-full flex flex-col justify-between font-mono select-none text-left p-2 overflow-hidden space-y-2">
      {/* Terminal Top Bar */}
      <div className="flex items-center justify-between border-b border-cyan-500/20 pb-2 px-1">
        <div className="flex items-center gap-3">
          <div
            className={`w-2.5 h-2.5 rounded-full ${
              connected ? "bg-emerald-400 animate-pulse" : "bg-amber-400"
            }`}
          />
          <span className="text-xs font-bold text-cyan-300 tracking-wider">
            CHARLIE HOST TERMINAL // CONPTY
          </span>
          <span className="text-[10px] px-2 py-0.5 rounded bg-cyan-950/80 border border-cyan-500/30 text-cyan-200">
            {shellName.toUpperCase()}
          </span>
        </div>
        <div className="flex items-center gap-3 text-[10px]">
          <span className="text-slate-400">
            PID: <strong className="text-cyan-200 font-mono">{pid ?? "..."}</strong>
          </span>
          <span className="text-slate-500">|</span>
          <span className={connected ? "text-emerald-400" : "text-amber-400"}>
            {sessionStatus.toUpperCase()}
          </span>
          {!connected && (
            <button
              type="button"
              onClick={handleReconnect}
              className="px-2 py-0.5 rounded bg-cyan-950 border border-cyan-500/40 text-cyan-300 hover:bg-cyan-900 transition cursor-pointer text-[10px]"
            >
              Reconnect
            </button>
          )}
          <button
            type="button"
            onClick={handleInterrupt}
            title="Send Ctrl+C"
            className="px-2 py-0.5 rounded bg-slate-900 border border-cyan-500/30 text-slate-300 hover:text-cyan-200 hover:border-cyan-400 transition cursor-pointer text-[10px]"
          >
            Ctrl+C
          </button>
        </div>
      </div>

      {/* xterm DOM Container */}
      <div
        ref={containerRef}
        className="flex-1 w-full p-2.5 rounded-xl border border-cyan-500/30 bg-slate-950/90 backdrop-blur-md overflow-hidden text-xs shadow-inner"
        style={{ minHeight: 0 }}
      />
    </div>
  );
}
