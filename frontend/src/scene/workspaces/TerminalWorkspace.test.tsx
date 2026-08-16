import { describe, expect, test, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { TerminalWorkspace } from "./TerminalWorkspace";
import type { WorkspaceInstance } from "../../layout/workspaceStore";

// Mock WebSocket
class MockWebSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;
  static instances: MockWebSocket[] = [];
  url: string;
  readyState: number = 1;
  onopen: (() => void) | null = null;
  onmessage: ((e: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  sentData: string[] = [];

  constructor(url: string) {
    this.url = url;
    MockWebSocket.instances.push(this);
    setTimeout(() => {
      if (this.onopen) this.onopen();
      if (this.onmessage) {
        this.onmessage({
          data: JSON.stringify({
            type: "terminal_init",
            session_id: "primary",
            pid: 12345,
            shell: "powershell.exe",
            status: "running",
            cols: 80,
            rows: 24,
            scrollback: "PS C:\\Users\\Charlie> ",
          }),
        });
      }
    }, 10);
  }

  send(data: string) {
    this.sentData.push(data);
  }

  close() {
    this.readyState = 3;
    if (this.onclose) this.onclose();
  }
}

describe("TerminalWorkspace Component", () => {
  beforeEach(() => {
    MockWebSocket.instances = [];
    vi.stubGlobal("WebSocket", MockWebSocket);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  const mockWorkspace: WorkspaceInstance = {
    id: "terminal",
    presentationIntentId: "intent-term",
    taskId: "task-term",
    title: "TERMINAL",
    summary: "Host Shell",
    type: "terminal",
    status: "active",
    lifecycleState: "active",
    focused: true,
    openedAt: new Date().toISOString(),
    lastFocusedAt: new Date().toISOString(),
    persistent: true,
    replayable: false,
    contentState: {},
  };

  test("renders terminal workspace header and connects to websocket", async () => {
    render(<TerminalWorkspace workspace={mockWorkspace} />);

    expect(screen.getByText(/CHARLIE HOST TERMINAL/i)).toBeDefined();
    expect(screen.getByText(/POWERSHELL.EXE/i)).toBeDefined();
    expect(screen.getByText("Ctrl+C")).toBeDefined();

    // Verify WebSocket instance was created with /ws/terminal/primary
    expect(MockWebSocket.instances.length).toBeGreaterThan(0);
    expect(MockWebSocket.instances[0].url).toContain("/ws/terminal/primary");
  });

  test("handles Ctrl+C interrupt button click", async () => {
    render(<TerminalWorkspace workspace={mockWorkspace} />);
    const interruptBtn = screen.getByText("Ctrl+C");
    fireEvent.click(interruptBtn);

    const ws = MockWebSocket.instances[0];
    expect(ws.sentData).toContain(JSON.stringify({ type: "interrupt" }));
  });
});
