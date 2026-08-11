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
});
