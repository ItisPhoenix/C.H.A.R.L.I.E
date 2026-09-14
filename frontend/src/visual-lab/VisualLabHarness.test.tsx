import { describe, expect, test } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { VisualLabHarness, type VisualLabScenario } from "./VisualLabHarness";
import { REFERENCE_VISUAL_SCENARIOS } from "./ReferenceVisualLabTruthful";

const scenarios: VisualLabScenario[] = [
  "idle", "listening", "transcribing", "thinking", "acting", "speaking", "approval", "error", "recovery",
  "conversation", "research", "briefing", "system", "tasks", "settings", "offline", "degraded",
  "conversation-rich", "research-rich", "briefing-rich", "system-rich", "tasks-rich",
  "spatial-idle", "spatial-research", "spatial-research-selected", "spatial-vision", "spatial-vision-selected",
  ...REFERENCE_VISUAL_SCENARIOS,
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

  test("uses isolated spatial proof path instead of production CharlieScene", async () => {
    const { container } = render(
      <MemoryRouter>
        <VisualLabHarness scenario="spatial-research" />
      </MemoryRouter>,
    );

    expect(await screen.findByRole("main", { name: "TEST/MOCK adaptive spatial canvas" })).toBeInTheDocument();
    expect(container.querySelector(".spatial-dominant-surface")).toBeInTheDocument();
    expect(container.querySelector("[data-testid=\"research-context-field\"]")).toBeInTheDocument();
    expect(container.querySelector(".charlie-workspace-layer")).toBeNull();
    expect(container.querySelector("[data-visual-lab=\"TEST/MOCK\"]")).toBeInTheDocument();
  });

  test("preserves research base while opening and popping selected context", async () => {
    const { container } = render(
      <MemoryRouter>
        <VisualLabHarness scenario="spatial-research" />
      </MemoryRouter>,
    );

    const select = await screen.findByTestId("research-select-finding");
    expect(container.querySelector('[data-context-depth="1"]')).toBeInTheDocument();
    fireEvent.click(select);
    expect(await screen.findByRole("region", { name: /Recovery improved after storage upgrades/i })).toBeInTheDocument();
    expect(container.querySelector('[data-dominant-surface="research-map"]')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("research-open-analysis"));
    expect(container.querySelector('[data-context-depth="3"]')).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Close WHY THIS SIGNAL MATTERS/i }));
    expect(container.querySelector('[data-context-depth="2"]')).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Close Recovery improved/i }));
    expect(container.querySelector('[data-context-depth="1"]')).toBeInTheDocument();
  });

  test("renders the vision proof frame with selectable grounded regions", async () => {
    const { container } = render(
      <MemoryRouter>
        <VisualLabHarness scenario="spatial-vision-selected" />
      </MemoryRouter>,
    );

    expect(await screen.findByAltText("TEST/MOCK local vision frame")).toBeInTheDocument();
    expect(container.querySelectorAll(".spatial-vision-box")).toHaveLength(3);
    expect(await screen.findByRole("region", { name: "SELECTED REGION" })).toBeInTheDocument();
    expect(container.querySelector('[data-core-position="dock_bottom_right"]')).toBeInTheDocument();
  });

  test.each(REFERENCE_VISUAL_SCENARIOS)("renders %s as an isolated reference screen", async (scenario) => {
    const { container } = render(
      <MemoryRouter>
        <VisualLabHarness scenario={scenario} />
      </MemoryRouter>,
    );

    expect(container.querySelector(`[data-reference-scenario="${scenario}"]`)).toBeInTheDocument();
    expect(container.querySelector(`[data-reference-screen="${scenario}"]`)).toBeInTheDocument();
    expect(container.querySelector('[data-reference-core="authoritative-charlie-ring"]')).toBeInTheDocument();
    expect(await screen.findByText("C.H.A.R.L.I.E.")).toBeInTheDocument();
  });

  test("keeps selected research context truthful when no live finding exists", async () => {
    const { container } = render(
      <MemoryRouter>
        <VisualLabHarness scenario="ref-research-selected" />
      </MemoryRouter>,
    );

    expect(container.querySelector('[data-reference-screen="ref-research-selected"]')).toBeInTheDocument();
    expect(await screen.findByText("SELECTED CONTEXT")).toBeInTheDocument();
    expect(await screen.findByText("NO SELECTED FINDING AVAILABLE")).toBeInTheDocument();
    expect((await screen.findAllByText("DATA CONTRACT MISSING / NOT WIRED")).length).toBeGreaterThan(0);
    expect(container.querySelector(".ref-selected-callout")).toBeNull();
  });

  test("does not infer temperature or fan telemetry from unrelated fields", async () => {
    const { container } = render(
      <MemoryRouter>
        <VisualLabHarness scenario="ref-online" />
      </MemoryRouter>,
    );

    expect([...container.querySelectorAll(".ref-online-system dd")].map((node) => node.textContent)).toEqual([
      "UNAVAILABLE",
      "UNAVAILABLE",
    ]);
  });

  test("keeps docked reference core ring-only", async () => {
    const { container } = render(
      <MemoryRouter>
        <VisualLabHarness scenario="ref-active-docked" />
      </MemoryRouter>,
    );

    expect(await screen.findByText("C.H.A.R.L.I.E.")).toBeInTheDocument();
    expect(container.querySelector(".ref-core--dock .ref-core__status")).toBeNull();
  });
});
