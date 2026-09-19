import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { CharlieCore, RADIAL_DIVISION_COUNT } from "./CharlieCore";

describe("CharlieCore", () => {
  it("renders accessible vector identity geometry", () => {
    const { container } = render(<CharlieCore />);
    const svg = container.querySelector("svg");

    expect(svg?.getAttribute("role")).toBe("img");
    expect(container.querySelectorAll("[data-core-tick]")).toHaveLength(
      RADIAL_DIVISION_COUNT,
    );
    expect(container.querySelectorAll("[data-core-arc]")).toHaveLength(3);
    expect(container.querySelectorAll("[data-core-energy-segment]")).toHaveLength(2);
    expect(container.querySelectorAll("[data-core-inner-arc]")).toHaveLength(0);
    expect(container.querySelectorAll("circle.core__energized-ring")).toHaveLength(1);
    expect(container.querySelector("[data-core-energy]")).not.toBeNull();
    expect(container.querySelector('[data-core-arc="primary"]')?.getAttribute("stroke-dasharray"))
      .toBe("11 89");
    expect(container.querySelector(".core__identity-point")).toBeNull();
    expect(container.querySelector(".core__ring--outer")).toBeNull();
    expect(container.querySelector(".core__ring--track")).toBeNull();
    expect(container.querySelector(".core__orbit--outer")).toBeNull();
    expect(container.querySelector("[data-core-inner-ring]")).not.toBeNull();
    expect(container.querySelector(".core-state")).toBeNull();
    expect(container.textContent).toContain("CHARLIE");
  });
});
