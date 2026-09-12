import type { ReactElement, ReactNode } from "react";

interface SpatialOverlayProps {
  id: string;
  eyebrow: string;
  title: string;
  position?: "right" | "bottom";
  onClose: () => void;
  children: ReactNode;
}

export function SpatialOverlay({
  id,
  eyebrow,
  title,
  position = "right",
  onClose,
  children,
}: SpatialOverlayProps): ReactElement {
  return (
    <aside
      className={`spatial-overlay spatial-overlay--${position}`}
      data-spatial-overlay={id}
      role="region"
      aria-label={title}
    >
      <div className="spatial-overlay__header">
        <div>
          <div className="spatial-overlay__eyebrow">{eyebrow}</div>
          <h2>{title}</h2>
        </div>
        <button type="button" className="spatial-overlay__close" onClick={onClose} aria-label={`Close ${title}`}>
          ×
        </button>
      </div>
      <div className="spatial-overlay__body">{children}</div>
    </aside>
  );
}
