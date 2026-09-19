import { act, cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { useRef } from "react";
import { useSpatialLayout } from "./useSpatialLayout";

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it("disables reposition animation and cancels active motion when the OS preference changes", async () => {
  let notifyResize = () => {};
  let notifyPreference = () => {};
  let reduced = false;
  let height = 70;
  const cancel = vi.fn();
  const animate = vi.fn(() => ({ cancel, onfinish: null }));
  vi.stubGlobal("ResizeObserver", class {
    constructor(callback: () => void) { notifyResize = callback; }
    observe() {}
    disconnect() {}
  });
  vi.stubGlobal("matchMedia", () => ({
    get matches() { return reduced; },
    addEventListener: (_: string, callback: () => void) => { notifyPreference = callback; },
    removeEventListener() {},
  }));
  vi.spyOn(HTMLElement.prototype, "offsetWidth", "get").mockReturnValue(100);
  vi.spyOn(HTMLElement.prototype, "offsetHeight", "get").mockImplementation(function(this: HTMLElement) {
    return this.dataset.spatialId === "result" ? height : 100;
  });
  vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockReturnValue(600);
  vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockReturnValue(400);
  Object.defineProperty(HTMLElement.prototype, "animate", { value: animate, configurable: true });
  const items = [{ id: "result", importance: 90 }, { id: "charlie", importance: 65, core: true }];
  function Harness() {
    const ref = useRef<HTMLDivElement>(null);
    useSpatialLayout(ref, items);
    return <div><div ref={ref} style={{ "--spatial-gap": "20px" } as React.CSSProperties}>
      <div data-spatial-id="result" /><div data-spatial-id="charlie" />
    </div></div>;
  }
  const { container } = render(<Harness />);
  await waitFor(() => expect(container.querySelector('[data-measured="true"]')).not.toBeNull());
  height = 300;
  act(() => notifyResize());
  await waitFor(() => expect(animate).toHaveBeenCalled());
  act(() => { reduced = true; notifyPreference(); });
  expect(cancel).toHaveBeenCalled();
  animate.mockClear();
  height = 120;
  act(() => notifyResize());
  await waitFor(() => expect(container.querySelector('[data-spatial-id="result"]')?.getAttribute("data-measured-height")).toBe("120"));
  expect(animate).not.toHaveBeenCalled();
  delete (HTMLElement.prototype as Partial<HTMLElement>).animate;
});
