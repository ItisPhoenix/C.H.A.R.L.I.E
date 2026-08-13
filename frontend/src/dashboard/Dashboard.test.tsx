import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, test } from "vitest";
import { useCharlieStore } from "../store/charlie";
import { Dashboard } from "./Dashboard";

beforeEach(() => {
  useCharlieStore.setState({ connected: false, chatMessages: [], mcpStatus: {}, systemStatus: null, tasks: {} });
});

describe("Dashboard", () => {
  test("does not render unsupported demo panels", () => {
    render(<Dashboard />);

    expect(screen.queryByText("Echoes of Tomorrow")).not.toBeInTheDocument();
    expect(screen.queryByText("Charlie OS v1.0.0")).not.toBeInTheDocument();
    expect(screen.queryByText("Threat Scan")).not.toBeInTheDocument();
  });
});
