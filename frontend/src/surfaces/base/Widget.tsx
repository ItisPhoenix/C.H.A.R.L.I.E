import type { ReactElement } from "react";
import type { SurfaceSpec } from "../../store/charlie";
import { Frame } from "./Frame";

export function Widget({ spec }: { spec: SurfaceSpec }): ReactElement {
  return (
    <Frame spec={spec}>
      {spec.body && <div className="text-xs text-[var(--color-text-secondary)] whitespace-pre-wrap flex-1 overflow-y-auto">{spec.body}</div>}
    </Frame>
  );
}
