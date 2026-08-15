import type { ReactElement, ReactNode } from "react";
import type { LayoutSpec } from "../surfaceSchema";

interface LayoutContainerProps {
  layout?: LayoutSpec;
  children: ReactNode;
  className?: string;
}

export function LayoutContainer({
  layout = { type: "stack", gap: 12 },
  children,
  className = "",
}: LayoutContainerProps): ReactElement {
  const type = layout.type || "stack";
  const gap = Math.min(Math.max(4, layout.gap ?? 12), 48);
  const columns = Math.min(Math.max(1, layout.columns ?? 2), 6);

  if (type === "row") {
    return (
      <div
        className={`flex flex-wrap items-center ${className}`}
        style={{ gap: `${gap}px` }}
      >
        {children}
      </div>
    );
  }

  if (type === "grid" || type === "columns") {
    return (
      <div
        className={`grid w-full ${className}`}
        style={{
          gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))`,
          gap: `${gap}px`,
        }}
      >
        {children}
      </div>
    );
  }

  if (type === "section") {
    return (
      <section
        className={`p-3 rounded-xl bg-slate-950/40 border border-cyan-500/15 flex flex-col ${className}`}
        style={{ gap: `${gap}px` }}
      >
        {children}
      </section>
    );
  }

  // Default: stack
  return (
    <div
      className={`flex flex-col w-full ${className}`}
      style={{ gap: `${gap}px` }}
    >
      {children}
    </div>
  );
}
