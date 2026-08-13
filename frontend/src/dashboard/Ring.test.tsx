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

    expect(screen.getByText("Offline")).toBeInTheDocument();
    expect(screen.queryByText("Online")).not.toBeInTheDocument();
  });

  test("exposes real microphone energy to the reactive ring", () => {
    useCharlieStore.setState({ connected: true, coreState: "listening", audioLevel: 0.72 });
    render(<Ring />);

    expect(screen.getByRole("img")).toHaveAttribute("data-audio-level", "0.72");
  });
});
