import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SettingsSectionNav } from "./SettingsSectionNav";

describe("settings section menu", () => {
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

  it("shows every category and routes a selected menu to its section", () => {
    const onSelect = vi.fn();
    act(() => root.render(createElement(SettingsSectionNav, { active: "presence", onSelect })));

    expect([...host.querySelectorAll("button")].map((button) => button.textContent)).toEqual([
      "Presence",
      "Voice",
      "Runtime",
      "Privacy",
    ]);
    expect(host.querySelector('[aria-pressed="true"]')?.getAttribute("data-section")).toBe("presence");
    act(() => host.querySelector<HTMLButtonElement>('[data-section="runtime"]')!.click());
    expect(onSelect).toHaveBeenCalledWith("runtime");
  });
});
