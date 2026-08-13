import { beforeEach, describe, expect, test } from "vitest";
import { useCharlieStore } from "./charlie";

beforeEach(() => {
  useCharlieStore.setState({
    connected: false,
    coreState: "idle",
    activities: [],
    widgets: {},
    modals: {},
    workspaces: {},
    notifications: {},
    activeToolApproval: null,
    systemStatus: null,
    netHistory: [],
    subsystemHealth: {},
    tasks: {},
    chatMessages: [],
  });
});

describe("applyEvent", () => {
  test("charlie_state updates coreState and activities", () => {
    useCharlieStore.getState().applyEvent({
      type: "charlie_state",
      payload: { state: "thinking", activities: ["voice_turn"] },
    });
    expect(useCharlieStore.getState().coreState).toBe("thinking");
    expect(useCharlieStore.getState().activities).toEqual(["voice_turn"]);
  });

  test("surface_spawn adds a spec to the matching presentation map", () => {
    useCharlieStore.getState().applyEvent({
      type: "surface_spawn",
      payload: { surface_id: "s1", presentation: "widget", persistence: "ephemeral", density: 2, region: "top_right", rationale: "test" },
    });
    expect(useCharlieStore.getState().widgets["s1"]?.rationale).toBe("test");
  });

  test("surface_dismiss removes the spec from whichever map holds it", () => {
    useCharlieStore.getState().applyEvent({
      type: "surface_spawn",
      payload: { surface_id: "s1", presentation: "modal", persistence: "persistent", density: 4, region: "" },
    });
    useCharlieStore.getState().applyEvent({ type: "surface_dismiss", payload: { surface_id: "s1" } });
    expect(useCharlieStore.getState().modals["s1"]).toBeUndefined();
  });

  test("tool_approval_request sets activeToolApproval", () => {
    useCharlieStore.getState().applyEvent({
      type: "tool_approval_request",
      payload: { request_id: "r1", tool_name: "shell_execute", reason: "gated", arguments: { command: "ls" } },
    });
    expect(useCharlieStore.getState().activeToolApproval?.request_id).toBe("r1");
  });

  test("tool_approval_resolved clears activeToolApproval for the matching request", () => {
    useCharlieStore.getState().applyEvent({
      type: "tool_approval_request",
      payload: { request_id: "r1", tool_name: "shell_execute", reason: "gated", arguments: {} },
    });
    useCharlieStore.getState().applyEvent({ type: "tool_approval_resolved", payload: { request_id: "r1" } });
    expect(useCharlieStore.getState().activeToolApproval).toBeNull();
  });

  test("tool_approval_resolved for a stale request_id does not clear a newer one", () => {
    useCharlieStore.getState().applyEvent({
      type: "tool_approval_request",
      payload: { request_id: "r2", tool_name: "shell_execute", reason: "gated", arguments: {} },
    });
    useCharlieStore.getState().applyEvent({ type: "tool_approval_resolved", payload: { request_id: "r1" } });
    expect(useCharlieStore.getState().activeToolApproval?.request_id).toBe("r2");
  });

  test("audio and microphone events retain only explicit runtime state", () => {
    useCharlieStore.getState().applyEvent({ type: "audio_state", payload: { muted: true, volume: 0.4 } });
    useCharlieStore.getState().applyEvent({ type: "mic_state", payload: { mic_muted: true } });

    expect(useCharlieStore.getState().audioState).toEqual({ muted: true, volume: 0.4 });
    expect(useCharlieStore.getState().micMuted).toBe(true);
  });

  test("audio level keeps bounded live microphone energy for reactive HUD motion", () => {
    useCharlieStore.getState().applyEvent({ type: "audio_level", payload: { level: 1.8 } });

    expect(useCharlieStore.getState().audioLevel).toBe(1);
  });

  test("retains only registered dashboard panel visibility intents", () => {
    useCharlieStore.getState().applyEvent({ type: "dashboard_panel", payload: { action: "show", panel_id: "terminal" } });

    expect(useCharlieStore.getState().dashboardPanelIntent).toEqual({ action: "show", panelId: "terminal" });
  });

  test("dashboard visibility follows the pet toggle event", () => {
    useCharlieStore.getState().applyEvent({ type: "dashboard_visibility", payload: { visible: false } });
    expect(useCharlieStore.getState().dashboardVisible).toBe(false);
  });

  test("addUserMessage appends a user chat message", () => {
    useCharlieStore.getState().addUserMessage("hello");
    const messages = useCharlieStore.getState().chatMessages;
    expect(messages).toHaveLength(1);
    expect(messages[0]).toMatchObject({ role: "user", text: "hello", pending: false });
  });

  test("token events accumulate into one pending charlie message", () => {
    useCharlieStore.getState().applyEvent({ type: "token", payload: { text: "Hel" } });
    useCharlieStore.getState().applyEvent({ type: "token", payload: { text: "lo." } });
    const messages = useCharlieStore.getState().chatMessages;
    expect(messages).toHaveLength(1);
    expect(messages[0]).toMatchObject({ role: "charlie", text: "Hello.", pending: true });
  });

  test("response_done finalizes the pending charlie message", () => {
    useCharlieStore.getState().applyEvent({ type: "token", payload: { text: "Hi." } });
    useCharlieStore.getState().applyEvent({ type: "response_done", payload: {} });
    expect(useCharlieStore.getState().chatMessages[0].pending).toBe(false);
  });

  test("a token after response_done starts a new message, not appending to the finalized one", () => {
    useCharlieStore.getState().applyEvent({ type: "token", payload: { text: "First." } });
    useCharlieStore.getState().applyEvent({ type: "response_done", payload: {} });
    useCharlieStore.getState().applyEvent({ type: "token", payload: { text: "Second." } });
    const messages = useCharlieStore.getState().chatMessages;
    expect(messages).toHaveLength(2);
    expect(messages[1]).toMatchObject({ text: "Second.", pending: true });
  });

  test("alert sets activeAlert with severity and message", () => {
    useCharlieStore.getState().applyEvent({ type: "alert", payload: { severity: "warning", message: "CPU high" } });
    expect(useCharlieStore.getState().activeAlert).toMatchObject({ severity: "warning", message: "CPU high" });
  });

  test("dismissAlert clears activeAlert", () => {
    useCharlieStore.getState().applyEvent({ type: "alert", payload: { severity: "warning", message: "CPU high" } });
    useCharlieStore.getState().dismissAlert();
    expect(useCharlieStore.getState().activeAlert).toBeNull();
  });

  test("task_snapshot replaces task state with public runtime tasks", () => {
    useCharlieStore.getState().applyEvent({
      type: "task_snapshot",
      payload: {
        tasks: [{ id: "t1", title: "Check deployment", status: "running", current_step: 1, total_steps: 2 }],
      },
    });
    expect(useCharlieStore.getState().tasks).toEqual({
      t1: { id: "t1", title: "Check deployment", status: "running", currentStep: 1, totalSteps: 2 },
    });
  });

  test("background_task updates one task without exposing raw errors", () => {
    useCharlieStore.getState().applyEvent({
      type: "background_task",
      payload: {
        id: "t1", title: "Check deployment", status: "failed", current_step: 1, total_steps: 2,
        error: "api-key=secret",
      },
    });
    expect(useCharlieStore.getState().tasks.t1).toEqual({
      id: "t1", title: "Check deployment", status: "failed", currentStep: 1, totalSteps: 2,
    });
  });

  test("subsystem_health stores only public health state", () => {
    useCharlieStore.getState().applyEvent({
      type: "subsystem_health",
      payload: { voice: { status: "degraded", detail: "Unavailable" } },
    });
    expect(useCharlieStore.getState().subsystemHealth.voice).toEqual({
      status: "degraded", detail: "Unavailable",
    });
  });

  test("system_status preserves missing telemetry as unavailable", () => {
    useCharlieStore.getState().applyEvent({ type: "system_status", payload: {} });

    expect(useCharlieStore.getState().systemStatus).toEqual({
      cpu: null,
      ram: null,
      gpu: null,
      netKbps: null,
      uptimeSeconds: null,
      batteryPercent: null,
    });
  });
});
