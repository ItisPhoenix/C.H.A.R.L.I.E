import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ResearchResultData } from "./runtime";
import { ResearchTabs, researchDisplayTitle } from "./ResearchTabs";

describe("research tabs", () => {
  let host: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
    host = document.createElement("div");
    document.body.appendChild(host);
    root = createRoot(host);
  });

  afterEach(() => {
    act(() => root.unmount());
    host.remove();
    vi.unstubAllGlobals();
  });

  it("shortens request framing without changing the underlying query", () => {
    const query = "Research current official guidance on accessible disclosure controls and summarize it with sources.";
    expect(researchDisplayTitle(query)).toBe("Official guidance accessible");
  });

  it("keeps long tab titles concise across unrelated topics", () => {
    const weatherQuestion = "Research current NOAA guidance on preparing for a heat wave and summarize it with sources.";
    const pythonQuestion = "Find official Python steps for handling a Unicode encoding error and summarize it with sources.";
    expect(researchDisplayTitle(weatherQuestion)).toBe("NOAA guidance preparing heat");
    expect(researchDisplayTitle(pythonQuestion)).toBe("Official Python steps handling");
  });

  it("keeps each result selectable and dismissible on its own", () => {
    const results: ResearchResultData[] = [
      { id: "result-1", query: "First task", text: "First answer" },
      { id: "result-2", query: "Second task", text: "Second answer" },
    ];
    const onSelect = vi.fn();
    const onDismiss = vi.fn();
    act(() => root.render(createElement(ResearchTabs, {
      results,
      selectedId: "result-2",
      onSelect,
      onDismiss,
    })));

    expect([...host.querySelectorAll(".research-tab__open")].map((button) => button.textContent)).toEqual([
      "ResearchFirst task",
      "ResearchSecond task",
    ]);
    expect(host.querySelector("nav.research-tabs--dock")).toBeTruthy();
    expect(host.querySelector('[data-result-id="result-2"] .research-tab__open')?.getAttribute("aria-pressed")).toBe("true");
    act(() => host.querySelector<HTMLButtonElement>('[data-result-id="result-1"] .research-tab__open')!.click());
    act(() => host.querySelector<HTMLButtonElement>('[data-result-id="result-2"] .research-tab__dismiss')!.click());
    expect(onSelect).toHaveBeenCalledWith("result-1");
    expect(onDismiss).toHaveBeenCalledWith("result-2");

  });

  it("keeps voice-origin results in a compact Research tab without exposing channel labels", () => {
    const results: ResearchResultData[] = [
      { id: "voice-report", query: "Compare two approaches", text: "Answer", channel: "voice", searchResultCount: 32, documentCount: 30, sources: [{ id: "S1", title: "Evidence", url: "https://example.com/evidence" }] },
      { id: "chat-report", query: "Explain the result", text: "Answer", channel: "web", searchResultCount: 3, documentCount: 2 },
    ];
    act(() => root.render(createElement(ResearchTabs, { results, selectedId: null, onSelect: vi.fn(), onDismiss: vi.fn() })));

    const voiceCard = host.querySelector('[data-result-id="voice-report"]')!;
    expect(voiceCard.classList.contains("research-tab--voice")).toBe(true);
    expect(voiceCard.querySelector(".research-tab__open")?.children).toHaveLength(2);
    expect(voiceCard.querySelector(".research-reopen-tag")?.textContent).toBe("Research");
    expect(voiceCard.querySelector(".research-reopen-query")?.textContent).toBe("Compare two approaches");
    expect(voiceCard.querySelector(".research-tab__metrics")).toBeNull();
    expect(voiceCard.querySelector(".research-tab__action")).toBeNull();
    expect(voiceCard.querySelector(".research-tab__open")?.getAttribute("aria-label")).toContain("Open research answer");
    expect(voiceCard.querySelector(".research-tab__dismiss")?.getAttribute("aria-label")).toContain("Dismiss research");
    expect(voiceCard.textContent).not.toContain("Voice research");
    expect(host.querySelector('[data-result-id="chat-report"]')?.classList.contains("research-tab--voice")).toBe(false);
  });
});
