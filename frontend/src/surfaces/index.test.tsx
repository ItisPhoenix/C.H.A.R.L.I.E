import { beforeEach, describe, expect, test } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { useCharlieStore } from "../store/charlie";
import { SurfaceRoute } from "./index";

function renderSurface(id: string) {
  return render(
    <MemoryRouter initialEntries={[`/surface/${id}`]}>
      <Routes>
        <Route path="/surface/:surfaceId" element={<SurfaceRoute />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  useCharlieStore.setState({ widgets: {}, modals: {}, workspaces: {}, notifications: {}, activeToolApproval: null });
});

describe("SurfaceRoute", () => {
  test("renders Widget for a spawned widget surface", () => {
    useCharlieStore.getState().applyEvent({
      type: "surface_spawn",
      payload: { surface_id: "w1", presentation: "widget", persistence: "ephemeral", density: 1, region: "top_right", rationale: "system alert", title: "System Update" },
    });
    renderSurface("w1");
    expect(screen.getByText("System Update")).toBeInTheDocument();
    expect(screen.getByText("system alert")).toBeInTheDocument();
  });

  test("renders ToolApprovalDialog for the modal whose surface_id matches the approval's request_id", () => {
    // surface_id == request_id by construction (main.py spawns the modal keyed by request_id).
    useCharlieStore.getState().applyEvent({
      type: "surface_spawn",
      payload: { surface_id: "r1", presentation: "modal", persistence: "persistent", density: 4, region: "", rationale: "approval" },
    });
    useCharlieStore.getState().applyEvent({
      type: "tool_approval_request",
      payload: { request_id: "r1", tool_name: "shell_execute", reason: "gated", arguments: {} },
    });
    renderSurface("r1");
    expect(screen.getByText("Approval Required")).toBeInTheDocument();
  });

  test("renders the generic modal, not ToolApprovalDialog, when the active approval is for a different surface", () => {
    useCharlieStore.getState().applyEvent({
      type: "surface_spawn",
      payload: { surface_id: "m1", presentation: "modal", persistence: "persistent", density: 4, region: "", rationale: "approval" },
    });
    useCharlieStore.getState().applyEvent({
      type: "tool_approval_request",
      payload: { request_id: "r1", tool_name: "shell_execute", reason: "gated", arguments: {} },
    });
    renderSurface("m1");
    expect(screen.queryByText("Approval Required")).not.toBeInTheDocument();
  });

  test("renders nothing for an unknown surface id", () => {
    const { container } = renderSurface("missing");
    expect(container).toBeEmptyDOMElement();
  });
});
