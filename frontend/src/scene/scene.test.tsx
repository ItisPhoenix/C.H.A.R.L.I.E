import { describe, expect, test, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { useCharlieStore } from "../store/charlie";
import { CharlieScene } from "./CharlieScene";
import { EnvironmentLayer } from "./EnvironmentLayer";
import { WorkspaceLayer } from "./WorkspaceLayer";
import { ContentMaskLayer } from "./ContentMaskLayer";

import { useWorkspaceStore } from "../layout/workspaceStore";
import { useWidgetStore } from "../layout/widgetStore";

beforeEach(() => {
  localStorage.clear();
  useCharlieStore.setState({
    connected: true,
    hudVisible: true,
    coreState: "idle",
    presentationIntents: {},
    activeCaption: null,
    audioLevel: 0,
  });
  useWorkspaceStore.setState({
    workspaces: {},
    activeWorkspaceId: null,
    recentWorkspaces: [],
  });
  useWidgetStore.setState({
    widgets: {},
    topZIndex: 10,
    focusedWidgetId: null,
    pinnedLayouts: {},
  });
});

describe("CharlieScene spatial projection & layers", () => {
  test("idle scene centers core with no active workspace", () => {
    render(
      <MemoryRouter>
        <CharlieScene />
      </MemoryRouter>
    );

    const main = screen.getByRole("main");
    expect(main.getAttribute("data-scene-mode")).toBe("idle");
    expect(main.getAttribute("data-core-position")).toBe("center");
    expect(screen.queryByRole("region", { name: /primary workspace/i })).toBeNull();
  });

  test("workspace intent causes core to dock and workspace layer to activate", () => {
    useCharlieStore.getState().applyEvent({
      type: "presentation_intent",
      payload: {
        id: "ws-research-1",
        kind: "workspace",
        title: "Deep Research: Quantum Computing",
        summary: "Quantum advantage in error mitigation",
        workspace_type: "research",
        replayable: true,
      },
    });

    render(
      <MemoryRouter>
        <CharlieScene />
      </MemoryRouter>
    );

    const main = screen.getByRole("main");
    expect(main.getAttribute("data-scene-mode")).toBe("active");
    expect(main.getAttribute("data-core-position")).toBe("dock_bottom_right");

    const ws = screen.getByRole("region", { name: /primary workspace/i });
    expect(ws).toBeDefined();
    expect(screen.getAllByText(/Deep Research: Quantum Computing/i).length).toBeGreaterThan(0);
  });

  test("removing workspace intent restores core to center", () => {
    useCharlieStore.getState().applyEvent({
      type: "presentation_intent",
      payload: {
        id: "ws-briefing-1",
        kind: "workspace",
        title: "Daily Briefing",
        summary: "Calendar and news",
        workspace_type: "briefing",
      },
    });

    const { rerender } = render(
      <MemoryRouter>
        <CharlieScene />
      </MemoryRouter>
    );

    expect(screen.getByRole("main").getAttribute("data-core-position")).toBe("dock_bottom_right");

    // Dismiss workspace
    useCharlieStore.getState().applyEvent({
      type: "presentation_dismiss",
      payload: { id: "ws-briefing-1" },
    });

    rerender(
      <MemoryRouter>
        <CharlieScene />
      </MemoryRouter>
    );

    expect(screen.getByRole("main").getAttribute("data-core-position")).toBe("center");
    expect(screen.getByRole("main").getAttribute("data-scene-mode")).toBe("idle");
  });

  test("caption text renders in ContextLayer", () => {
    useCharlieStore.getState().applyEvent({
      type: "presentation_intent",
      payload: {
        id: "cap-1",
        kind: "caption",
        summary: "Volume set to 50%",
        caption_text: "Volume set to 50%",
      },
    });

    render(
      <MemoryRouter>
        <CharlieScene />
      </MemoryRouter>
    );

    const caption = screen.getByRole("status");
    expect(caption).toBeDefined();
    expect(screen.getByText("Volume set to 50%")).toBeDefined();
  });

  test("attention intent renders modal backdrop with high priority alert", () => {
    useCharlieStore.getState().applyEvent({
      type: "presentation_intent",
      payload: {
        id: "att-1",
        kind: "attention",
        title: "CONFIRM DELETION",
        summary: "Delete /tmp/build directory?",
        attention_level: "high",
      },
    });

    render(
      <MemoryRouter>
        <CharlieScene />
      </MemoryRouter>
    );

    const dialog = screen.getByRole("alertdialog");
    expect(dialog).toBeDefined();
    expect(screen.getByText(/CONFIRM DELETION/i)).toBeDefined();
    expect(screen.getByText(/Delete \/tmp\/build directory\?/i)).toBeDefined();
  });

  test("minimize and restore workspace recenters then re-docks core", () => {
    useWorkspaceStore.getState().openWorkspace({
      id: "ws-conv-proof",
      kind: "workspace",
      title: "Conversation",
      summary: "Session history",
      workspaceType: "conversation",
      taskId: "task-conv",
      content: {},
      priority: 50,
      attentionLevel: "normal",
      dismissPolicy: "persistent",
      preferredZone: "center",
      anchor: "screen",
      createdAt: new Date().toISOString(),
      replayable: false,
    });

    const { rerender } = render(
      <MemoryRouter>
        <CharlieScene />
      </MemoryRouter>
    );

    expect(screen.getByRole("main").getAttribute("data-core-position")).toBe("dock_bottom_right");

    useWorkspaceStore.getState().minimizeWorkspace("ws-conv-proof");
    rerender(
      <MemoryRouter>
        <CharlieScene />
      </MemoryRouter>
    );
    expect(screen.getByRole("main").getAttribute("data-core-position")).toBe("center");

    useWorkspaceStore.getState().restoreWorkspace("ws-conv-proof");
    rerender(
      <MemoryRouter>
        <CharlieScene />
      </MemoryRouter>
    );
    expect(screen.getByRole("main").getAttribute("data-core-position")).toBe("dock_bottom_right");
  });

  test("Escape key dismisses active workspace", () => {
    useCharlieStore.getState().applyEvent({
      type: "presentation_intent",
      payload: {
        id: "ws-esc-test",
        kind: "workspace",
        title: "Terminal Session",
        summary: "PowerShell active",
      },
    });

    render(
      <MemoryRouter>
        <CharlieScene />
      </MemoryRouter>
    );

    expect(useCharlieStore.getState().presentationIntents["ws-esc-test"]).toBeDefined();

    fireEvent.keyDown(window, { key: "Escape" });

    expect(useCharlieStore.getState().presentationIntents["ws-esc-test"]).toBeUndefined();
  });

  test("EnvironmentLayer renders technical framing and dual grid", () => {
    const { container } = render(
      <EnvironmentLayer corePosition="center" hasWorkspace={false} />
    );

    expect(container.querySelector(".charlie-env-grid")).not.toBeNull();
    expect(container.querySelector(".charlie-env-vignette")).not.toBeNull();
    expect(container.querySelector(".charlie-env-frame")).not.toBeNull();
  });

  test("ContentMaskLayer applies mask container classes", () => {
    const { container } = render(
      <ContentMaskLayer fadeEdges={true}>
        <div>Embedded Content</div>
      </ContentMaskLayer>
    );

    const mask = container.querySelector(".charlie-mask-container");
    expect(mask).not.toBeNull();
    expect(mask?.classList.contains("charlie-mask-fade-edges")).toBe(true);
  });

  test("WorkspaceLayer renders active workspace and handles dismiss", () => {
    let dismissedId = "";
    const mockWs = {
      id: "ws-direct-1",
      type: "terminal",
      presentationIntentId: "ws-direct-1",
      taskId: null,
      title: "Direct Workspace Test",
      summary: "Testing direct component render",
      status: "active",
      lifecycleState: "active" as const,
      focused: true,
      openedAt: new Date().toISOString(),
      lastFocusedAt: new Date().toISOString(),
      persistent: false,
      replayable: true,
      contentState: {},
    };

    render(
      <WorkspaceLayer activeWorkspace={mockWs} onDismiss={(id) => { dismissedId = id; }} />
    );

    expect(screen.getByText("Direct Workspace Test")).toBeDefined();
    const closeBtn = screen.getByRole("button", { name: /close/i });
    fireEvent.click(closeBtn);
    expect(dismissedId).toBe("ws-direct-1");
  });
});
