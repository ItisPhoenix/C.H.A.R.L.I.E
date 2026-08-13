import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, test } from "vitest";
import { useCharlieStore } from "../store/charlie";
import { McpConnections } from "./McpConnections";
import { useLayoutStore } from "./layoutStore";

beforeEach(() => {
  useCharlieStore.setState({ mcpStatus: {} });
  useLayoutStore.getState().open("mcp");
});

describe("McpConnections", () => {
  test("shows no connections instead of a fabricated server count", () => {
    render(<McpConnections />);

    expect(screen.getByText("No MCP servers reported.")).toBeInTheDocument();
    expect(screen.queryByText("8 Connected")).not.toBeInTheDocument();
  });
});
