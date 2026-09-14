import type { ReactElement, ReactNode } from "react";

export interface ContextLayerEntry {
  id: string;
  label: string;
}

interface ContextLayerStackProps {
  stack: readonly ContextLayerEntry[];
  children: (entry: ContextLayerEntry) => ReactNode;
}

export function ContextLayerStack({ stack, children }: ContextLayerStackProps): ReactElement {
  const active = stack.at(-1);
  return (
    <div
      className="spatial-context-layer-stack"
      data-context-depth={stack.length}
      data-context-id={active?.id || "none"}
      data-context-label={active?.label || "none"}
    >
      {active ? children(active) : null}
    </div>
  );
}
