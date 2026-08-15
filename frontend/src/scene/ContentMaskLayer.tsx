import type { ReactElement, ReactNode } from "react";

interface ContentMaskProps {
  children: ReactNode;
  fadeEdges?: boolean;
  className?: string;
}

export function ContentMaskLayer({ children, fadeEdges = true, className = "" }: ContentMaskProps): ReactElement {
  return (
    <div className={`charlie-mask-container ${fadeEdges ? "charlie-mask-fade-edges" : ""} ${className}`}>
      {children}
    </div>
  );
}
