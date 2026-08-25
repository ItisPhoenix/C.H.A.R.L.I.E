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
    activeToolApproval: null,
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
    expect(screen.getAllByTestId("charlie-core")).toHaveLength(1);
    expect(screen.getAllByTestId("charlie-core")[0].querySelectorAll('[data-core-renderer="authoritative-charlie-ring"]')).toHaveLength(1);
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

    const { container } = render(
      <MemoryRouter>
        <CharlieScene />
      </MemoryRouter>
    );

    const main = screen.getByRole("main");
    expect(main.getAttribute("data-scene-mode")).toBe("active");
    expect(main.getAttribute("data-core-position")).toBe("dock_bottom_right");

    const ws = screen.getByRole("region", { name: /primary workspace/i });
    expect(ws).toBeDefined();
    expect(ws).toHaveClass("charlie-workspace-research");
    expect(screen.getAllByTestId("charlie-core")).toHaveLength(1);
    expect(screen.getAllByTestId("charlie-core")[0].querySelectorAll('[data-core-renderer="authoritative-charlie-ring"]')).toHaveLength(1);
    expect(screen.getByText("RESEARCH & SYNTHESIS")).toBeInTheDocument();
    expect(screen.queryByText("Deep Research: Quantum Computing")).toBeNull();
    expect(container.querySelector(".charlie-panel")).toBeNull();
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

  test("live tool approval renders one authoritative dialog", () => {
    useCharlieStore.setState({
      activeToolApproval: {
        request_id: "approval-scene-1",
        tool_name: "shell_execute",
        reason: "Run the requested command.",
        arguments: { command: "python --version" },
        risk_class: "security_sensitive",
      },
    });
    useCharlieStore.getState().applyEvent({
      type: "presentation_intent",
      payload: {
        id: "approval-scene-1",
        kind: "attention",
        title: "Approval needed: shell_execute",
        summary: "Run the requested command.",
        attention_level: "high",
        content: { request_id: "approval-scene-1" },
      },
    });

    render(
      <MemoryRouter>
        <CharlieScene />
      </MemoryRouter>
    );

    expect(screen.getByTestId("tool-approval-overlay")).toBeInTheDocument();
    expect(screen.getAllByRole("dialog")).toHaveLength(1);
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(screen.getAllByRole("button", { name: "Approve & Run" })).toHaveLength(1);
    expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
    expect(screen.getByRole("button", { name: "Decline" })).toBeInTheDocument();
  });

  test("generic attention with request metadata stays non-actionable", () => {
    useCharlieStore.getState().applyEvent({
      type: "presentation_intent",
      payload: {
        id: "att-approval-context",
        kind: "attention",
        title: "Approval context",
        summary: "Runtime approval is handled elsewhere.",
        attention_level: "high",
        content: { request_id: "approval-context-1" },
      },
    });

    render(
      <MemoryRouter>
        <CharlieScene />
      </MemoryRouter>
    );

    expect(screen.getByRole("alertdialog")).toBeInTheDocument();
    expect(screen.getByText(/Approval handled by Charlie's approval dialog/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Decline" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Acknowledge" })).toBeNull();
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

    const { container } = render(
      <WorkspaceLayer activeWorkspace={mockWs} onDismiss={(id) => { dismissedId = id; }} />
    );

    expect(screen.getByRole("region", { name: "Primary Workspace Direct Workspace Test" })).toBeDefined();
    expect(screen.getByText("CHARLIE HOST TERMINAL // CONPTY")).toBeDefined();
    expect(screen.queryByText("Direct Workspace Test")).toBeNull();
    expect(container.querySelector(".charlie-panel")).toBeNull();
    const closeBtn = screen.getByRole("button", { name: /close/i });
    fireEvent.click(closeBtn);
    expect(dismissedId).toBe("ws-direct-1");
  });

  test("fresh startup renders idle spatial HUD immediately before any events", () => {
    // Fresh store instance with default initial values
    useCharlieStore.setState({
      connected: false,
      hudVisible: true,
      coreState: "idle",
      presentationIntents: {},
      activeCaption: null,
      audioLevel: 0,
    });

    render(
      <MemoryRouter>
        <CharlieScene />
      </MemoryRouter>
    );

    const main = screen.getByRole("main");
    expect(main).toBeInTheDocument();
    expect(main.getAttribute("data-scene-mode")).toBe("idle");
    expect(main.getAttribute("data-core-position")).toBe("center");
  });

  test("HUD hide and summon lifecycle preserves visibility state", () => {
    const { unmount, rerender } = render(
      <MemoryRouter>
        <CharlieScene />
      </MemoryRouter>
    );

    expect(screen.getByRole("main")).toBeInTheDocument();

    // Pet or voice hides HUD
    useCharlieStore.getState().applyEvent({ type: "hud_visibility", payload: { visible: false } });
    rerender(
      <MemoryRouter>
        <CharlieScene />
      </MemoryRouter>
    );
    expect(screen.queryByRole("main")).toBeNull();

    // User summons HUD again
    useCharlieStore.getState().applyEvent({ type: "hud_visibility", payload: { visible: true } });
    rerender(
      <MemoryRouter>
        <CharlieScene />
      </MemoryRouter>
    );
    expect(screen.getByRole("main")).toBeInTheDocument();
    unmount();
  });

  test("listening state keeps core centered and renders LISTENING and AWAITING INPUT typography", () => {
    useCharlieStore.getState().applyEvent({
      type: "charlie_state",
      payload: { state: "listening", activities: ["Awaiting voice input"] },
    });

    render(
      <MemoryRouter>
        <CharlieScene />
      </MemoryRouter>
    );

    const main = screen.getByRole("main");
    expect(main.getAttribute("data-core-position")).toBe("center");
    expect(main.getAttribute("data-core-state")).toBe("listening");
    expect(screen.getByText("LISTENING")).toBeInTheDocument();
    expect(screen.getByText("AWAITING INPUT")).toBeInTheDocument();
  });

  test("docked core in active workspace renders core-only with no status bar", () => {
    useCharlieStore.getState().applyEvent({
      type: "presentation_intent",
      payload: {
        id: "ws-research-docked-test",
        kind: "workspace",
        title: "RESEARCH // AGENTIC OS",
        summary: "Testing core only docking",
        workspace_type: "research",
      },
    });

    const { container } = render(
      <MemoryRouter>
        <CharlieScene />
      </MemoryRouter>
    );

    const coreWrapper = container.querySelector(".charlie-core-wrapper");
    expect(coreWrapper).toHaveClass("charlie-core-docked");

    // Docked mode must contain NO status bar, subtitle, or dots
    expect(container.querySelector(".charlie-core-status-bar")).toBeNull();
    expect(screen.queryByText("IDLE")).toBeNull();
    expect(screen.queryByText("I'M HERE WHEN YOU NEED ME.")).toBeNull();
  });

  test("clicking core opens contextual menu with Recent Workspaces, Settings, and Clear Screen", () => {
    render(
      <MemoryRouter>
        <CharlieScene />
      </MemoryRouter>
    );

    const coreButton = screen.getByRole("button", { name: /Charlie core in idle state/i });
    fireEvent.click(coreButton);

    expect(screen.getByText("Recent Workspaces")).toBeInTheDocument();
    expect(screen.getByText("Settings")).toBeInTheDocument();
    expect(screen.getByText("Clear Screen")).toBeInTheDocument();
    expect(screen.getByText("Close Menu")).toBeInTheDocument();
  });
});
