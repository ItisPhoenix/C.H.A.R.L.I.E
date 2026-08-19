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

  test("maintains absolute containment structure with hud-ring, canvas, and svg", () => {
    const { container } = render(<Ring />);

    const ring = container.querySelector(".hud-ring");
    const canvas = container.querySelector("canvas.hud-core-canvas");
    const svg = container.querySelector("svg.hud-outer-svg");

    expect(ring).toBeInTheDocument();
    expect(canvas).toBeInTheDocument();
    expect(svg).toBeInTheDocument();
    expect(ring?.contains(canvas)).toBe(true);
    expect(ring?.contains(svg)).toBe(true);
  });
});
