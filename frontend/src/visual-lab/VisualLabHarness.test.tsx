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

  test("provides populated contract-shaped Settings fixtures only in Visual Lab", async () => {
    const { container } = render(
      <MemoryRouter>
        <VisualLabHarness scenario="settings" />
      </MemoryRouter>,
    );

    expect(container.querySelector('[data-visual-lab-settings-fixture="contract-valid"]')).toBeInTheDocument();
    expect(await screen.findByRole("textbox", { name: "LLM Endpoint" })).toHaveValue("https://example.test/charlie/mock-gateway/v1");
    expect(screen.getByRole("combobox", { name: "LLM Model" })).toHaveValue("test/mock-charlie-operator");
    expect(screen.getByRole("spinbutton", { name: "Context Window" })).toHaveValue(32768);
    expect(screen.getByRole("checkbox", { name: "Vision Enabled" })).toBeChecked();
    expect(await screen.findByText("2 provider models available.")).toBeInTheDocument();
    expect(screen.getByTestId("authoritative-settings")).toHaveAttribute("data-active-category", "Models");
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
