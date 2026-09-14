import type { ReactElement, ReactNode } from "react";

export type DominantSurfaceKind = "research" | "vision";

interface DominantSurfaceProps {
  id: string;
  kind: DominantSurfaceKind;
  ariaLabel: string;
  children: ReactNode;
}

export function DominantSurface({ id, kind, ariaLabel, children }: DominantSurfaceProps): ReactElement {
  return (
    <section
      className={`spatial-dominant-surface spatial-dominant-surface--${kind}`}
      data-dominant-surface={id}
      data-surface-kind={kind}
      aria-label={ariaLabel}
    >
      {children}
    </section>
  );
}
