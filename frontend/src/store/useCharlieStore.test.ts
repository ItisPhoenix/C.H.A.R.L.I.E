import { describe, it, expect, beforeEach } from "vitest";
import { useCharlieStore } from "./useCharlieStore";
import type { Message } from "./useCharlieStore";

describe("useCharlieStore", () => {
  beforeEach(() => {
    // Reset store between tests
    useCharlieStore.setState({
      connected: false,
      sessions: [],
      currentSessionId: "",
      messages: [],
      messagesLoading: false,
      searchQuery: "",
      alerts: [],
      logs: [],
      activeTab: "dashboard",
      blackboard: { tasks: [], agents: {} },
      voiceState: "idle",
    });
  });

  describe("connection state", () => {
    it("toggles connected flag", () => {
      const { setConnected } = useCharlieStore.getState();
      setConnected(true);
      expect(useCharlieStore.getState().connected).toBe(true);
      setConnected(false);
      expect(useCharlieStore.getState().connected).toBe(false);
    });
  });

  describe("sessions", () => {
    it("sets sessions list", () => {
      const sessions = [
        { id: "s1", title: "Chat 1", created_at: "2026-01-01" },
        { id: "s2", title: "Chat 2", created_at: "2026-01-02" },
      ];
      useCharlieStore.getState().setSessions(sessions);
      expect(useCharlieStore.getState().sessions).toEqual(sessions);
    });

    it("sets current session id", () => {
      useCharlieStore.getState().setCurrentSessionId("s42");
      expect(useCharlieStore.getState().currentSessionId).toBe("s42");
    });
  });

  describe("messages", () => {
    it("replaces messages list", () => {
      const msgs: Message[] = [
        { role: "user", content: "hello" },
        { role: "assistant", content: "hi" },
      ];
      useCharlieStore.getState().setMessages(msgs);
      expect(useCharlieStore.getState().messages).toEqual(msgs);
    });

    it("appends a single message", () => {
      useCharlieStore.getState().setMessages([]);
      useCharlieStore.getState().addMessage({ role: "user", content: "a" });
      useCharlieStore.getState().addMessage({ role: "assistant", content: "b" });
      const { messages } = useCharlieStore.getState();
      expect(messages).toHaveLength(2);
      expect(messages[0].content).toBe("a");
      expect(messages[1].content).toBe("b");
    });

    it("updates last message content in place", () => {
      useCharlieStore.getState().setMessages([
        { role: "user", content: "q" },
        { role: "assistant", content: "partial" },
      ]);
      useCharlieStore.getState().updateLastMessageContent("full answer");
      const { messages } = useCharlieStore.getState();
      expect(messages).toHaveLength(2);
      expect(messages[0].content).toBe("q"); // untouched
      expect(messages[1].content).toBe("full answer"); // updated
    });

    it("handles updateLastMessageContent with empty array gracefully", () => {
      useCharlieStore.getState().setMessages([]);
      useCharlieStore.getState().updateLastMessageContent("should not crash");
      expect(useCharlieStore.getState().messages).toHaveLength(0);
    });

    it("preserves immutability on updateLastMessageContent", () => {
      const original: Message[] = [
        { role: "user", content: "q" },
        { role: "assistant", content: "old" },
      ];
      useCharlieStore.getState().setMessages(original);
      useCharlieStore.getState().updateLastMessageContent("new");
      // Original array should not be mutated
      expect(original[1].content).toBe("old");
    });
  });

  describe("alerts", () => {
    it("prepends alert and caps at 100", () => {
      const { addAlert } = useCharlieStore.getState();

      // Add 105 alerts
      for (let i = 0; i < 105; i++) {
        addAlert({ severity: "info", message: `alert-${i}`, timestamp: `t${i}` });
      }

      const { alerts } = useCharlieStore.getState();
      expect(alerts).toHaveLength(100);
      // Most recent first
      expect(alerts[0].message).toBe("alert-104");
      expect(alerts[99].message).toBe("alert-5");
    });
  });

  describe("logs", () => {
    it("prepends log and caps at 500", () => {
      const { addLog } = useCharlieStore.getState();

      for (let i = 0; i < 510; i++) {
        addLog(`log-${i}`);
      }

      const { logs } = useCharlieStore.getState();
      expect(logs).toHaveLength(500);
      expect(logs[0]).toBe("log-509");
    });
  });

  describe("blackboard", () => {
    it("sets blackboard state", () => {
      const bb = {
        tasks: [{ id: "t1", description: "task1", status: "running" as const }],
        agents: { jarvis: { name: "jarvis", role: "orchestrator", status: "active" } },
      };
      useCharlieStore.getState().setBlackboard(bb);
      expect(useCharlieStore.getState().blackboard).toEqual(bb);
    });
  });

  describe("voice state", () => {
    it("sets voice state", () => {
      useCharlieStore.getState().setVoiceState("listening");
      expect(useCharlieStore.getState().voiceState).toBe("listening");
    });
  });

  describe("system status", () => {
    it("sets system status", () => {
      const status = { cpu: 45.2, ram: 68.1, gpu: 30.0, active_agents: ["jarvis"] };
      useCharlieStore.getState().setSystemStatus(status);
      expect(useCharlieStore.getState().systemStatus).toEqual(status);
    });
  });
});
