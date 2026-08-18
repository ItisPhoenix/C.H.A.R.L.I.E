import { beforeEach, describe, expect, test } from "vitest";
import { useCharlieStore } from "./charlie";

beforeEach(() => {
  useCharlieStore.setState({
    connected: false,
    coreState: "idle",
    activities: [],
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

  test("presentation intents are the only surface state projection", () => {
    useCharlieStore.getState().applyEvent({
      type: "presentation_intent",
      payload: { id: "intent-1", kind: "workspace", workspace_type: "terminal", title: "TERMINAL" },
    });
    expect(useCharlieStore.getState().presentationIntents["intent-1"]?.workspaceType).toBe("terminal");
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

test("HUD visibility follows the pet toggle event", () => {
    useCharlieStore.getState().applyEvent({ type: "hud_visibility", payload: { visible: false } });
    expect(useCharlieStore.getState().hudVisible).toBe(false);
  });

  test("initial HUD visibility is consistent main web frontend", () => {
    expect(useCharlieStore.getState().hudVisible).toBe(false);
  });

  test("repeated HUD summon does not duplicate", () => {
    useCharlieStore.getState().applyEvent({ type: "hud_visibility", payload: { visible: true } });
    expect(useCharlieStore.getState().hudVisible).toBe(true);
    useCharlieStore.getState().applyEvent({ type: "hud_visibility", payload: { visible: true } });
    expect(useCharlieStore.getState().hudVisible).toBe(true);
    useCharlieStore.getState().applyEvent({ type: "hud_visibility", payload: { visible: false } });
    expect(useCharlieStore.getState().hudVisible).toBe(false);
    useCharlieStore.getState().applyEvent({ type: "hud_visibility", payload: { visible: true } });
    expect(useCharlieStore.getState().hudVisible).toBe(true);
  });

  test("closed/disconnected HUD can reopen", () => {
    useCharlieStore.getState().applyEvent({ type: "hud_visibility", payload: { visible: false } });
    expect(useCharlieStore.getState().hudVisible).toBe(false);
    useCharlieStore.getState().applyEvent({ type: "hud_visibility", payload: { visible: true } });
    expect(useCharlieStore.getState().hudVisible).toBe(true);
    useCharlieStore.getState().applyEvent({ type: "hud_visibility", payload: { visible: false } });
    expect(useCharlieStore.getState().hudVisible).toBe(false);
    useCharlieStore.getState().applyEvent({ type: "hud_visibility", payload: { visible: true } });
    expect(useCharlieStore.getState().hudVisible).toBe(true);
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

  test("task state normalizes legacy lifecycle names at the runtime boundary", () => {
    useCharlieStore.getState().applyEvent({
      type: "background_task",
      payload: { id: "t1", title: "Check deployment", status: "done", current_step: 2, total_steps: 2 },
    });
    useCharlieStore.getState().applyEvent({
      type: "background_task",
      payload: { id: "t2", title: "Approve change", status: "awaiting_approval", current_step: 0, total_steps: 1 },
    });

    expect(useCharlieStore.getState().tasks.t1.status).toBe("completed");
    expect(useCharlieStore.getState().tasks.t2.status).toBe("approval_required");
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
      disk: null,
      netKbps: null,
      uptimeSeconds: null,
      batteryPercent: null,
    });
  });

  test("presentation_intent upserts intent and updates active caption", () => {
    useCharlieStore.getState().applyEvent({
      type: "presentation_intent",
      payload: {
        id: "pi_1",
        kind: "widget",
        title: "CPU Metric",
        summary: "CPU is at 15%",
        widget_type: "system_metric",
        auto_dismiss_ms: 5000,
        replace_key: "widget:system_metric",
      },
    });

    const stored = useCharlieStore.getState().presentationIntents["pi_1"];
    expect(stored).toBeDefined();
    expect(stored?.kind).toBe("widget");
    expect(stored?.widgetType).toBe("system_metric");
    expect(stored?.autoDismissMs).toBe(5000);
  });

  test("presentation_intent with replace_key replaces old intent with same key", () => {
    useCharlieStore.getState().applyEvent({
      type: "presentation_intent",
      payload: { id: "pi_cpu_1", kind: "widget", replace_key: "widget:system_metric", summary: "CPU 10%" },
    });
    useCharlieStore.getState().applyEvent({
      type: "presentation_intent",
      payload: { id: "pi_cpu_2", kind: "widget", replace_key: "widget:system_metric", summary: "CPU 15%" },
    });

    const intents = useCharlieStore.getState().presentationIntents;
    expect(intents["pi_cpu_1"]).toBeUndefined();
    expect(intents["pi_cpu_2"]?.summary).toBe("CPU 15%");
  });

  test("presentation_dismiss removes intent from state", () => {
    useCharlieStore.getState().applyEvent({
      type: "presentation_intent",
      payload: { id: "pi_dismiss", kind: "workspace", workspace_type: "research" },
    });
    expect(useCharlieStore.getState().presentationIntents["pi_dismiss"]).toBeDefined();

    useCharlieStore.getState().applyEvent({
      type: "presentation_dismiss",
      payload: { id: "pi_dismiss" },
    });
    expect(useCharlieStore.getState().presentationIntents["pi_dismiss"]).toBeUndefined();
  });
});
