import type { ReactElement, ReactNode } from "react";
import type { SurfaceSpec } from "../../store/charlie";

// showRationale defaults on (Workspace turns it off); header (status dot + title) lives here so severity reads at a glance, not just the 1px top rim.
export function Frame({ spec, children, showRationale = true }: { spec: SurfaceSpec; children: ReactNode; showRationale?: boolean }): ReactElement {
  return (
    <div className={`glass w-full h-full box-border p-4 flex flex-col gap-2 overflow-hidden role-${spec.role}`}>
      {spec.title && (
        <div className="flex items-center gap-2 shrink-0">
          <span className={`role-dot role-dot-${spec.role} w-1.5 h-1.5 rounded-full shrink-0`} aria-hidden="true" />
          <span className="text-sm font-semibold text-[var(--color-text-primary)] truncate">{spec.title}</span>
        </div>
      )}
      {children}
      {showRationale && <p className="mt-auto text-[10px] text-[var(--color-text-muted)] truncate">{spec.rationale}</p>}
    </div>
  );
}
