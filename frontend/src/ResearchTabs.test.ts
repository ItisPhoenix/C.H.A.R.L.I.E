import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ResearchResultData } from "./runtime";
import { ResearchTabs } from "./ResearchTabs";

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
    expect(host.querySelector('[data-result-id="result-2"] .research-tab__open')?.getAttribute("aria-pressed")).toBe("true");
    act(() => host.querySelector<HTMLButtonElement>('[data-result-id="result-1"] .research-tab__open')!.click());
    act(() => host.querySelector<HTMLButtonElement>('[data-result-id="result-2"] .research-tab__dismiss')!.click());
    expect(onSelect).toHaveBeenCalledWith("result-1");
    expect(onDismiss).toHaveBeenCalledWith("result-2");

    act(() => root.render(createElement(ResearchTabs, {
      results,
      selectedId: "result-2",
      onSelect,
      onDismiss,
      placement: "panel",
    })));
    expect(host.querySelector("nav.research-tabs--panel")?.getAttribute("aria-label")).toBe("Research results");
    expect(host.querySelectorAll(".research-tab__open")).toHaveLength(2);
  });
});
