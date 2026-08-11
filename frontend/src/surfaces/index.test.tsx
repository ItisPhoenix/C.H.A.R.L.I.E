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
      payload: { surface_id: "w1", presentation: "widget", persistence: "ephemeral", density: 1, region: "top_right", rationale: "system alert" },
    });
    renderSurface("w1");
    expect(screen.getByText("Widget")).toBeInTheDocument();
    expect(screen.getByText("system alert")).toBeInTheDocument();
  });

  test("renders ToolApprovalDialog for a modal surface when an approval is active", () => {
    useCharlieStore.getState().applyEvent({
      type: "surface_spawn",
      payload: { surface_id: "m1", presentation: "modal", persistence: "persistent", density: 4, region: "", rationale: "approval" },
    });
    useCharlieStore.getState().applyEvent({
      type: "tool_approval_request",
      payload: { request_id: "r1", tool_name: "shell_execute", reason: "gated", arguments: {} },
    });
    renderSurface("m1");
    expect(screen.getByText("Approval Required")).toBeInTheDocument();
  });

  test("renders nothing for an unknown surface id", () => {
    const { container } = renderSurface("missing");
    expect(container).toBeEmptyDOMElement();
  });
});
