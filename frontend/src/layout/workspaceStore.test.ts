import { describe, expect, test, beforeEach } from "vitest";
import { useWorkspaceStore } from "./workspaceStore";
import type { PresentationIntent } from "../store/charlie";

beforeEach(() => {
  useWorkspaceStore.setState({
    workspaces: {},
    activeWorkspaceId: null,
    recentWorkspaces: [],
  });
});

describe("WorkspaceManager Store & Lifecycle", () => {
  const mockIntent1: PresentationIntent = {
    id: "ws-research-1",
    kind: "workspace",
    title: "Quantum Research",
    summary: "Error mitigation algorithms",
    workspaceType: "research",
    taskId: "task-99",
    content: { query: "quantum computing" },
    priority: 50,
    attentionLevel: "normal",
    dismissPolicy: "manual",
    preferredZone: "center",
    anchor: "screen",
    createdAt: new Date().toISOString(),
    replayable: true,
  };

  const mockIntent2: PresentationIntent = {
    id: "ws-briefing-2",
    kind: "workspace",
    title: "Daily Briefing",
    summary: "Morning calendar and news",
    workspaceType: "briefing",
    taskId: null,
    content: {},
    priority: 40,
    attentionLevel: "normal",
    dismissPolicy: "manual",
    preferredZone: "center",
    anchor: "screen",
    createdAt: new Date().toISOString(),
    replayable: true,
  };

  test("openWorkspace creates active primary workspace instance", () => {
    const ws = useWorkspaceStore.getState().openWorkspace(mockIntent1);

    expect(ws.id).toBe("ws-research-1");
    expect(ws.type).toBe("research");
    expect(ws.lifecycleState).toBe("active");
    expect(ws.taskId).toBe("task-99");
    expect(useWorkspaceStore.getState().activeWorkspaceId).toBe("ws-research-1");
    expect(useWorkspaceStore.getState().getActiveWorkspace()?.id).toBe("ws-research-1");
  });

  test("opening second workspace minimizes first to recentWorkspaces", () => {
    useWorkspaceStore.getState().openWorkspace(mockIntent1);
    useWorkspaceStore.getState().openWorkspace(mockIntent2);

    expect(useWorkspaceStore.getState().activeWorkspaceId).toBe("ws-briefing-2");
    expect(useWorkspaceStore.getState().workspaces["ws-research-1"]?.lifecycleState).toBe("minimized");

    const recent = useWorkspaceStore.getState().recentWorkspaces;
    expect(recent.length).toBe(1);
    expect(recent[0]?.id).toBe("ws-research-1");
    expect(recent[0]?.taskId).toBe("task-99");
  });

  test("minimizeWorkspace clears active workspace and records in recent", () => {
    useWorkspaceStore.getState().openWorkspace(mockIntent1);
    useWorkspaceStore.getState().minimizeWorkspace("ws-research-1");

    expect(useWorkspaceStore.getState().activeWorkspaceId).toBeNull();
    expect(useWorkspaceStore.getState().getActiveWorkspace()).toBeNull();
    expect(useWorkspaceStore.getState().recentWorkspaces[0]?.id).toBe("ws-research-1");
  });

  test("restoreWorkspace restores minimized workspace to active primary", () => {
    useWorkspaceStore.getState().openWorkspace(mockIntent1);
    useWorkspaceStore.getState().minimizeWorkspace("ws-research-1");

    expect(useWorkspaceStore.getState().activeWorkspaceId).toBeNull();

    useWorkspaceStore.getState().restoreWorkspace("ws-research-1");

    expect(useWorkspaceStore.getState().activeWorkspaceId).toBe("ws-research-1");
    expect(useWorkspaceStore.getState().getActiveWorkspace()?.lifecycleState).toBe("active");
  });

  test("minimize and restore preserve runtime task and presentation identity", () => {
    const taskIntent = { ...mockIntent1, id: "task-workspace:task-99" };
    useWorkspaceStore.getState().openWorkspace(taskIntent);
    useWorkspaceStore.getState().minimizeWorkspace(taskIntent.id);
    useWorkspaceStore.getState().restoreWorkspace(taskIntent.id);

    const restored = useWorkspaceStore.getState().getActiveWorkspace();
    expect(restored?.id).toBe("task-workspace:task-99");
    expect(restored?.presentationIntentId).toBe("task-workspace:task-99");
    expect(restored?.taskId).toBe("task-99");
    expect(useWorkspaceStore.getState().workspaces).toHaveProperty("task-workspace:task-99");
  });

  test("closing workspace removes active presence while retaining task linkage in history", () => {
    useWorkspaceStore.getState().openWorkspace(mockIntent1);
    useWorkspaceStore.getState().closeWorkspace("ws-research-1");

    expect(useWorkspaceStore.getState().activeWorkspaceId).toBeNull();
    expect(useWorkspaceStore.getState().workspaces["ws-research-1"]).toBeUndefined();

    // Recent list retains metadata and task linkage
    const recent = useWorkspaceStore.getState().recentWorkspaces.find((r) => r.id === "ws-research-1");
    expect(recent).toBeDefined();
    expect(recent?.taskId).toBe("task-99");
  });

  test("clearWorkspaces minimizes active workspace", () => {
    useWorkspaceStore.getState().openWorkspace(mockIntent1);
    useWorkspaceStore.getState().clearWorkspaces();

    expect(useWorkspaceStore.getState().activeWorkspaceId).toBeNull();
    expect(useWorkspaceStore.getState().recentWorkspaces[0]?.id).toBe("ws-research-1");
  });
});
