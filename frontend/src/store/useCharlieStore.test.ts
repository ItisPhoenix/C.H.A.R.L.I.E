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
      alerts: [],
      logs: [],
      blackboard: { tasks: [], agents: {} },
      voiceState: "idle",
      audioLevel: 0,
      toolActivity: [],
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

    it("appends token content to the last assistant message", () => {
      useCharlieStore.getState().setMessages([
        { role: "user", content: "q" },
        { role: "assistant", content: "partial" },
      ]);
      useCharlieStore.getState().updateLastMessageContent("full answer");
      const { messages } = useCharlieStore.getState();
      expect(messages).toHaveLength(2);
      expect(messages[0].content).toBe("q"); // untouched
      expect(messages[1].content).toBe("partialfull answer"); // streamed tokens concatenate
    });

    it("handles updateLastMessageContent with empty array gracefully", () => {
      useCharlieStore.getState().setMessages([]);
      useCharlieStore.getState().updateLastMessageContent("should not crash");
      // No assistant message exists yet, so the token starts a new one.
      expect(useCharlieStore.getState().messages).toHaveLength(1);
      expect(useCharlieStore.getState().messages[0].role).toBe("assistant");
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
        tasks: [{ id: "t1", name: "task1", status: "running" as const }],
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

  describe("tool activity & audio level", () => {
    it("appendToolActivity adds entry and setAudioLevel updates", () => {
      const s = useCharlieStore.getState();
      s.appendToolActivity({ kind: "tool_call", name: "web_search", text: "ran" });
      expect(useCharlieStore.getState().toolActivity).toHaveLength(1);
      s.setAudioLevel(0.5);
      expect(useCharlieStore.getState().audioLevel).toBe(0.5);
    });

    it("clearToolActivity empties the list", () => {
      const s = useCharlieStore.getState();
      s.appendToolActivity({ kind: "tool_result", name: "web_search", text: "done" });
      expect(useCharlieStore.getState().toolActivity).toHaveLength(1);
      s.clearToolActivity();
      expect(useCharlieStore.getState().toolActivity).toHaveLength(0);
    });
  });
});
