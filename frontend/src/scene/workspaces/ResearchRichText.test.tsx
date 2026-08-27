import { act } from "react";
import { hydrateRoot } from "react-dom/client";
import { renderToString } from "react-dom/server";
import { render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { WorkspaceInstance } from "../../layout/workspaceStore";
import { ResearchWorkspace } from "./ResearchWorkspace";
import { ResearchRichText } from "./ResearchRichText";

function expectValidResearchMarkup(container: HTMLElement): void {
  expect(container.querySelector("p p")).toBeNull();
  expect(container.querySelector("p div")).toBeNull();
  expect(container.querySelector("p h1, p h2, p h3, p h4, p h5, p h6")).toBeNull();
}

describe("ResearchRichText DOM contract", () => {
  it.each([
    ["plain paragraph", "A plain research paragraph."],
    ["multiple paragraphs", "First paragraph.\n\nSecond paragraph."],
    ["markdown heading", "## Key finding"],
    ["bullet lines", "- First finding\n- Second finding"],
    ["bold", "A **high-confidence** finding."],
    ["inline code", "Use `charlie.research_workspace`."],
    ["link", "Read [the source](https://example.com/source)."],
    ["mixed block content", "# Summary\n\nA **finding** with `evidence`.\n\n- One\n- Two"],
  ])("renders %s without invalid block nesting", (_label, text) => {
    const { container } = render(<ResearchRichText text={text} />);
    expectValidResearchMarkup(container);
  });

  it("renders current visible objective content without invalid block nesting", () => {
    const workspace: WorkspaceInstance = {
      id: "research-dom-contract",
      presentationIntentId: "intent-research-dom-contract",
      taskId: "task-research-dom-contract",
      title: "RESEARCH",
      summary: "Workspace summary metadata",
      type: "research",
      status: "active",
      lifecycleState: "active",
      focused: true,
      openedAt: new Date().toISOString(),
      lastFocusedAt: new Date().toISOString(),
      persistent: false,
      replayable: false,
      contentState: {
        objective: "Paragraph with **bold** and `code`.",
      },
    };

    const { container } = render(<ResearchWorkspace workspace={workspace} />);
    expectValidResearchMarkup(container);
    const header = container.querySelector("header");
    expect(header).toBeVisible();
    expect(header).toHaveTextContent("RESEARCH OBJECTIVE");
    expect(header).toHaveTextContent("Paragraph with bold and code.");
    expect(container.querySelectorAll("p").length).toBeGreaterThan(0);
  });

  it("hydrates equivalent research markup without nesting or hydration warnings", async () => {
    const text = "# Summary\n\nParagraph with **bold**.\n\n- Finding";
    const container = document.createElement("div");
    container.innerHTML = renderToString(<ResearchRichText text={text} />);
    document.body.appendChild(container);
    const error = vi.spyOn(console, "error").mockImplementation(() => {});

    let root: ReturnType<typeof hydrateRoot> | undefined;
    try {
      await act(async () => {
        root = hydrateRoot(container, <ResearchRichText text={text} />);
      });
      expectValidResearchMarkup(container);
      expect(error).not.toHaveBeenCalled();
    } finally {
      root?.unmount();
      container.remove();
      error.mockRestore();
    }
  });
});
