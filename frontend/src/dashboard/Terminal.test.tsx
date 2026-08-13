import { render, screen } from "@testing-library/react";
import { describe, expect, test } from "vitest";
import { Terminal } from "./Terminal";

describe("Terminal", () => {
  test("does not fabricate shell output without a terminal contract", () => {
    render(<Terminal />);

    expect(screen.getByText("Terminal output is unavailable.")).toBeInTheDocument();
    expect(screen.queryByText("Charlie OS v1.0.0")).not.toBeInTheDocument();
    expect(screen.queryByText("12 active")).not.toBeInTheDocument();
  });
});
