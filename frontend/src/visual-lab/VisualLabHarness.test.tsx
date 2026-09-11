import { describe, expect, test } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { VisualLabHarness, type VisualLabScenario } from "./VisualLabHarness";

const scenarios: VisualLabScenario[] = [
  "idle", "listening", "transcribing", "thinking", "acting", "speaking", "approval", "error", "recovery",
  "conversation", "research", "briefing", "system", "tasks", "settings", "offline", "degraded",
  "conversation-rich", "research-rich", "briefing-rich", "system-rich", "tasks-rich",
];

describe("TEST/MOCK visual lab", () => {
  test.each(scenarios)("marks %s as non-runtime evidence", async (scenario) => {
    const { container } = render(
      <MemoryRouter>
        <VisualLabHarness scenario={scenario} />
      </MemoryRouter>,
    );
    expect(container.querySelector('[data-visual-lab="TEST/MOCK"]')).toBeInTheDocument();
    expect(await screen.findByText("TEST/MOCK VISUAL LAB — not runtime acceptance")).toBeInTheDocument();
  });

  test("provides dense contract-shaped research evidence for visual review", async () => {
    render(
      <MemoryRouter>
        <VisualLabHarness scenario="research-rich" />
      </MemoryRouter>,
    );

    expect(await screen.findByText("URBAN GRID RESILIENCE")).toBeInTheDocument();
    expect(await screen.findByText("Recovery improved after storage upgrades")).toBeInTheDocument();
    expect(await screen.findByText("EVIDENCE & SOURCES")).toBeInTheDocument();
  });

  test("provides concurrent task fixtures without entering production runtime", async () => {
    render(
      <MemoryRouter>
        <VisualLabHarness scenario="tasks-rich" />
      </MemoryRouter>,
    );

    expect((await screen.findAllByText("Prepare resilience briefing")).length).toBeGreaterThan(0);
    expect(await screen.findByText("CONCURRENT TASKS")).toBeInTheDocument();
    expect(screen.getByText("TEST/MOCK VISUAL LAB — not runtime acceptance")).toBeInTheDocument();
  });
});
