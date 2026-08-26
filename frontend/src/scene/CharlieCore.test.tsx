import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, test, vi } from "vitest";
import { useCharlieStore } from "../store/charlie";
import { CharlieCore } from "./CharlieCore";

beforeEach(() => {
  useCharlieStore.setState({ connected: true, coreState: "idle", audioLevel: 0 });
});

function renderCore(position: "center" | "dock_bottom_right", coreState = "idle") {
  useCharlieStore.setState({ coreState });
  return render(<CharlieCore position={position} coreState={coreState} />);
}

describe("CharlieCore authority", () => {
  test("centered mode renders one authoritative visual renderer and status metadata", () => {
    const { container } = renderCore("center");

    expect(screen.getAllByTestId("charlie-core")).toHaveLength(1);
    expect(container.querySelectorAll('[data-core-renderer="authoritative-charlie-ring"]')).toHaveLength(1);
    expect(container.querySelector(".charlie-core-status-bar")).toBeInTheDocument();
  });

  test("docked mode uses same renderer and omits status metadata", () => {
    const { container } = renderCore("dock_bottom_right", "working");

    expect(container.querySelectorAll('[data-core-renderer="authoritative-charlie-ring"]')).toHaveLength(1);
    expect(container.querySelector(".charlie-core-status-bar")).not.toBeInTheDocument();
    expect(container.querySelector(".charlie-core-state-label")).not.toBeInTheDocument();
    expect(container.querySelector(".charlie-core-state-subtext")).not.toBeInTheDocument();
  });

  test("runtime-projected state reaches the shared renderer", () => {
    const { container } = renderCore("center", "listening");

    expect(container.querySelector('[data-core-renderer="authoritative-charlie-ring"]')).toHaveAttribute("data-state", "listening");
  });

  test("idle menu exposes production conversation summon action", () => {
    const onOpenConversation = vi.fn();
    render(<CharlieCore position="center" coreState="idle" onOpenConversation={onOpenConversation} />);

    fireEvent.click(screen.getByRole("button", { name: /Charlie core in idle state/i }));
    fireEvent.click(screen.getByRole("button", { name: "Open Conversation" }));

    expect(onOpenConversation).toHaveBeenCalledTimes(1);
  });
});
