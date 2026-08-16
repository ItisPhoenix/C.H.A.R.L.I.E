import type { ReactElement, ReactNode } from "react";
import "./contextCard.css";

export type ContextCardVariant =
  | "compact"
  | "standard"
  | "location"
  | "event"
  | "source"
  | "warning";

export interface ContextCardProps {
  variant?: ContextCardVariant;
  children?: ReactNode;
  className?: string;
  onClick?: () => void;
  interactive?: boolean;
  elevation?: "flat" | "elevated" | "floating";
  role?: string;
  "aria-label"?: string;
}

export function ContextCard({
  variant = "standard",
  children,
  className = "",
  onClick,
  interactive = false,
  elevation = "elevated",
  role,
  "aria-label": ariaLabel,
}: ContextCardProps): ReactElement {
  const isClickable = Boolean(onClick) || interactive;

  return (
    <div
      role={role || (isClickable ? "button" : undefined)}
      aria-label={ariaLabel}
      onClick={onClick}
      tabIndex={isClickable ? 0 : undefined}
      onKeyDown={
        isClickable
          ? (e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                onClick?.();
              }
            }
          : undefined
      }
      className={`charlie-context-card variant-${variant} elevation-${elevation} ${
        isClickable ? "interactive" : ""
      } ${className}`}
    >
      {children}
    </div>
  );
}
