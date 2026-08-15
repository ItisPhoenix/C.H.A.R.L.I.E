import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, test } from "vitest";
import { useCharlieStore } from "../store/charlie";
import { Ring } from "./Ring";

beforeEach(() => {
  useCharlieStore.setState({ connected: false, coreState: "idle" });
});

describe("Ring", () => {
  test("shows offline when no runtime connection exists", () => {
    render(<Ring />);

    expect(screen.getByRole("img", { name: "Charlie Offline" })).toBeInTheDocument();
  });

  test("exposes real microphone energy to the reactive ring", () => {
    useCharlieStore.setState({ connected: true, coreState: "listening", audioLevel: 0.72 });
    render(<Ring />);

    expect(screen.getByRole("img")).toHaveAttribute("data-audio-level", "0.72");
  });

  test("renders the layered vector outer HUD", () => {
    const { container } = render(<Ring />);

    expect(container.querySelector(".hud-outer-svg")).toBeInTheDocument();
    expect(container.querySelectorAll(".hud-vector-ticks circle").length).toBeGreaterThan(0);
  });
});
