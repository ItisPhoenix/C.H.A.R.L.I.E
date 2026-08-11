import type { ReactElement, ReactNode } from "react";
import type { SurfaceSpec } from "../../store/charlie";

// showRationale defaults on; Workspace turns it off since it already renders spec.rationale as its own body.
export function Frame({ spec, children, showRationale = true }: { spec: SurfaceSpec; children: ReactNode; showRationale?: boolean }): ReactElement {
  return (
    <div className="glass w-full h-full box-border p-4 flex flex-col gap-2 overflow-hidden">
      {children}
      {showRationale && <p className="mt-auto text-[10px] text-[var(--color-text-muted)] truncate">{spec.rationale}</p>}
    </div>
  );
}
