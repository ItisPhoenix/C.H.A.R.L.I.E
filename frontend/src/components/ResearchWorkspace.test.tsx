import { fireEvent, render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ResearchWorkspace } from "./ResearchWorkspace";
import type { RuntimeResearch } from "../runtime/types";

const result = {
  schema: "charlie.research_workspace",
  version: 1,
  query: "Which option is supported?",
  objective: "Which option is supported?",
  mode: "standard",
  title: "Research & Synthesis",
  summary: "Option A is supported by the available evidence.",
  status: "complete",
  confidence: 0.8,
  plan: [{ id: "P1", title: "Compare sources", status: "complete" }],
  findings: [{ id: "F1", title: "Option A", detail: "Two sources support option A.", source_ids: ["S1", "S2"] }],
  sources: [
    { id: "S1", title: "Primary source", domain: "primary.example", snippet: "Primary evidence." },
    { id: "S2", title: "Independent source", domain: "independent.example", snippet: "Independent evidence." },
  ],
};

const research: RuntimeResearch = {
  progress: null,
  result,
  objective: "Which option is supported?",
  activity: [{ id: "a1", stage: "complete", message: "Answer synthesized." }],
  truth: "REAL RUNTIME",
};

describe("ResearchWorkspace", () => {
  it("keeps synthesis, evidence, citations, and source interaction in one composition", () => {
    const view = render(<ResearchWorkspace research={research} />);
    expect(view.getByText("Option A is supported by the available evidence.")).toBeTruthy();
    expect(view.getByText("Primary source")).toBeTruthy();
    expect(view.getByRole("button", { name: "S1" })).toBeTruthy();
    fireEvent.click(view.getByRole("button", { name: "S2" }));
    expect(view.getByText("S2 · Independent source")).toBeTruthy();
    expect(view.getByText("REAL RUNTIME")).toBeTruthy();
  });

  it("keeps follow-up and close actions limited to supplied capabilities", () => {
    const onSend = vi.fn(() => true);
    const onDismiss = vi.fn();
    const view = render(<ResearchWorkspace research={research} onSend={onSend} onDismiss={onDismiss} />);
    fireEvent.change(view.getByLabelText("FOLLOW UP"), { target: { value: "Explain source S1" } });
    fireEvent.click(view.getByRole("button", { name: "Send" }));
    fireEvent.click(view.getByRole("button", { name: "Close research ×" }));
    expect(onSend).toHaveBeenCalledWith("Explain source S1");
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });

  it("renders fixture truth explicitly", () => {
    const view = render(<ResearchWorkspace research={{ ...research, truth: "FIXTURE" }} truth="FIXTURE" />);
    expect(view.getByText("FIXTURE")).toBeTruthy();
  });

  it("keeps working research as a small pulse without empty dashboard sections", () => {
    const view = render(<ResearchWorkspace research={{
      progress: { stage: "searching", message: "Searching official sources…" },
      result: null,
      objective: "Which option is supported?",
      truth: "FIXTURE",
    }} compact truth="FIXTURE" />);
    expect(view.getByText("Searching official sources…")).toBeTruthy();
    expect(view.container.querySelector(".research-pulse")).toBeTruthy();
    expect(view.container.querySelector(".research-context")).toBeNull();
    expect(view.container.querySelector(".research-evidence")).toBeNull();
  });

  it("leads active evidence with the finding and hides source details until requested", () => {
    const view = render(<ResearchWorkspace research={{
      ...research,
      progress: { stage: "reading", message: "Reading source evidence", current: 2, total: 3 },
      result: {
        ...result,
        summary: "Evidence is being linked to the active findings.",
        status: "reading",
        findings: [result.findings[0]],
      },
    }} />);

    expect(view.container.querySelector("h1")?.textContent).toBe("Option A");
    expect(view.queryByText("Evidence is being linked to the active findings.")).toBeNull();
    expect(view.container.querySelector(".research-status-line")).toBeNull();

    const sources = view.container.querySelector(".research-evidence-disclosure") as HTMLDetailsElement;
    expect(sources).toBeTruthy();
    expect(sources.open).toBe(false);

    fireEvent.click(view.container.querySelector(".research-lead-support .research-citation") as HTMLButtonElement);
    expect(sources.open).toBe(true);
    expect(view.getByText("S1 · Primary source")).toBeTruthy();
  });

  it("renders a readable result instead of exposing raw markdown in the heading", () => {
    const view = render(<ResearchWorkspace research={{
      ...research,
      result: {
        ...result,
        title: "Research & Synthesis",
        summary: "Based on the latest data, **Current temp:** 24°C\n\n- **Tonight:** Clear skies",
      },
    }} />);

    expect(view.container.querySelector("h1")?.textContent).toBe("Research result");
    expect(view.container.querySelector(".research-summary")).toBeTruthy();
    expect(view.getByText("Current temp:").tagName).toBe("STRONG");
    expect(view.getByText("Tonight:").tagName).toBe("STRONG");
  });
});
