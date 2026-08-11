import type { ReactElement } from "react";
import type { SurfaceSpec } from "../../store/charlie";
import { Frame } from "./Frame";

export function Notification({ spec }: { spec: SurfaceSpec }): ReactElement {
  return (
    <Frame spec={spec}>
      <span className="text-sm font-medium text-[var(--color-accent)]">Notification</span>
    </Frame>
  );
}
