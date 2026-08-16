import type { ReactElement, ReactNode } from "react";

export interface ContextCardBodyProps {
  children?: ReactNode;
  text?: string;
  className?: string;
}

export function ContextCardBody({
  children,
  text,
  className = "",
}: ContextCardBodyProps): ReactElement {
  return (
    <div className={`charlie-card-body ${className}`}>
      {text && <p className="leading-relaxed text-slate-200">{text}</p>}
      {children}
    </div>
  );
}
