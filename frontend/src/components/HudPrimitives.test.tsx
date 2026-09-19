import { fireEvent, render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { HudChart, HudEvidence, HudHeading, HudIconButton, HudMetric, HudSource, HudTextInput, HudTimeline } from "./HudPrimitives";

describe("HUD visual primitives", () => {
  it("composes semantic typography, metrics, evidence, and charts without a card shell", () => {
    const view = render(<>
      <HudHeading label="SYNTHESIS">A focused answer</HudHeading>
      <HudMetric label="CPU" value="18.4" unit="%" />
      <HudEvidence><HudSource number="01" title="Runbook" detail="Timeout sequence." /></HudEvidence>
      <HudChart values={[1, 4, 2]} label="Observed trend" />
    </>);
    expect(view.getByText("SYNTHESIS")).toBeTruthy();
    expect(view.getByText("18.4")).toBeTruthy();
    expect(view.getByText("Runbook")).toBeTruthy();
    expect(view.getByRole("img", { name: "Observed trend" })).toBeTruthy();
    expect(view.container.querySelector("[class*='card']")).toBeNull();
  });

  it("provides shared interactive input, icon, and timeline primitives", () => {
    const positions: number[] = [];
    const onSeek = (position: number) => positions.push(position);
    const view = render(<>
      <HudTextInput aria-label="Shared input" />
      <HudIconButton label="Toggle tool" pressed>+</HudIconButton>
      <HudTimeline position={10} duration={60} label="0:10 of 1:00" onSeek={onSeek} />
    </>);
    expect(view.getByRole("textbox", { name: "Shared input" })).toBeTruthy();
    expect(view.getByRole("button", { name: "Toggle tool" }).getAttribute("data-pressed")).toBe("true");
    fireEvent.change(view.getByRole("slider", { name: "0:10 of 1:00" }), { target: { value: "24" } });
    expect(positions).toEqual([24]);
  });
});
