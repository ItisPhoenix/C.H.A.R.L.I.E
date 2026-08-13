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
});
