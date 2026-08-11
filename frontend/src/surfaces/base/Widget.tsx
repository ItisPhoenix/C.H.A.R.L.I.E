import type { ReactElement } from "react";
import type { SurfaceSpec } from "../../store/charlie";
import { Frame } from "./Frame";

export function Widget({ spec }: { spec: SurfaceSpec }): ReactElement {
  return (
    <Frame spec={spec}>
      <span className="text-sm font-medium text-[var(--color-text-primary)]">Widget</span>
    </Frame>
  );
}
