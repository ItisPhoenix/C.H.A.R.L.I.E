import type { CSSProperties, InputHTMLAttributes, ReactNode } from "react";

export function HudLabel({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <p className={`hud-label ${className}`.trim()}>{children}</p>;
}

export function HudHeading({ label, children, className = "" }: {
  label?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return <div className={`hud-heading ${className}`.trim()}>
    {label && <HudLabel>{label}</HudLabel>}
    <h2>{children}</h2>
  </div>;
}

export function HudHairline({ className = "" }: { className?: string }) {
  return <span className={`hud-hairline ${className}`.trim()} aria-hidden="true" />;
}

export function HudCornerFragment({ className = "" }: { className?: string }) {
  return <span className={`hud-corner-fragment ${className}`.trim()} aria-hidden="true" />;
}

export function HudMetric({ label, value, unit }: { label: ReactNode; value: ReactNode; unit?: ReactNode }) {
  return <div className="hud-metric"><HudLabel>{label}</HudLabel><strong>{value}</strong>{unit && <span>{unit}</span>}</div>;
}

export function HudProgress({ value, label, detail }: { value?: number; label?: ReactNode; detail?: ReactNode }) {
  const width = typeof value === "number" && Number.isFinite(value) ? Math.max(0, Math.min(100, value)) : 0;
  return <div className="hud-progress">
    {(label || detail) && <div className="hud-progress__meta"><span>{label}</span><span>{detail}</span></div>}
    <span className="hud-progress__track"><span style={{ width: `${width}%` }} /></span>
  </div>;
}

export function HudTimeline({ position = 0, duration = 0, label, onSeek }: {
  position?: number;
  duration?: number;
  label?: ReactNode;
  onSeek?: (position: number) => void;
}) {
  const progress = duration > 0 ? Math.max(0, Math.min(100, position / duration * 100)) : 0;
  const value = duration > 0 ? Math.max(0, Math.min(duration, position)) : 0;
  if (onSeek && duration > 0) {
    return <input type="range" className="hud-timeline hud-timeline--interactive" min="0" max={duration}
      step="1" value={value} aria-label={typeof label === "string" ? label : undefined}
      aria-valuetext={typeof label === "string" ? label : undefined}
      style={{ "--hud-timeline-position": `${progress}%` } as CSSProperties}
      onChange={event => onSeek(Number(event.target.value))} />;
  }
  return <div className="hud-timeline" aria-label={typeof label === "string" ? label : undefined}>
    <span style={{ left: `${progress}%` }} />
  </div>;
}

export function HudTextInput({ className = "", ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={`hud-input ${className}`.trim()} />;
}

export function HudIconButton({ children, label, title, disabled = false, pressed, onClick }: {
  children: ReactNode;
  label: string;
  title?: string;
  disabled?: boolean;
  pressed?: boolean;
  onClick?: () => void;
}) {
  return <button type="button" role="button" className="hud-icon-button" aria-label={label} title={title ?? label}
    data-pressed={pressed === undefined ? undefined : pressed} disabled={disabled} onClick={onClick}>
    {children}
  </button>;
}

export function HudSource({ number, title, detail }: { number?: ReactNode; title: ReactNode; detail?: ReactNode }) {
  return <li className="hud-source"><span>{number}</span><div><strong>{title}</strong>{detail && <p>{detail}</p>}</div></li>;
}

export function HudEvidence({ children }: { children: ReactNode }) {
  return <ol className="hud-evidence">{children}</ol>;
}

export function HudChart({ values, label }: { values: readonly number[]; label: string }) {
  if (!values.length) return null;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const points = values.map((value, index) => `${(index / Math.max(1, values.length - 1)) * 100},${100 - ((value - min) / span) * 84 - 8}`).join(" ");
  return <svg className="hud-chart" viewBox="0 0 100 100" role="img" aria-label={label} preserveAspectRatio="none">
    <polyline points={points} />
  </svg>;
}

export function HudImageFrame({ children, src, alt = "" }: { children?: ReactNode; src?: string; alt?: string }) {
  return <figure className="hud-image-frame">{src ? <img src={src} alt={alt} /> : children}</figure>;
}

export function HudMedia({ title, artist, children }: { title: ReactNode; artist?: ReactNode; children?: ReactNode }) {
  return <div className="hud-media"><HudLabel>MEDIA</HudLabel><strong>{title}</strong>{artist && <span>{artist}</span>}{children}</div>;
}

export function HudTerminal({ children }: { children: ReactNode }) {
  return <pre className="hud-terminal">{children}</pre>;
}

export function HudDocumentSnippet({ title, children }: { title: ReactNode; children: ReactNode }) {
  return <div className="hud-document-snippet"><HudLabel>{title}</HudLabel><div>{children}</div></div>;
}

export function HudStatus({ children, tone = "neutral" }: { children: ReactNode; tone?: "neutral" | "success" | "warning" | "error" }) {
  return <p className={`hud-status hud-status--${tone}`}><span aria-hidden="true" />{children}</p>;
}

export function HudAction({ children, onClick, disabled = false, className = "", "aria-label": ariaLabel, title, type = "button" }: {
  children: ReactNode;
  onClick?: () => void;
  disabled?: boolean;
  className?: string;
  "aria-label"?: string;
  title?: string;
  type?: "button" | "submit";
}) {
  return <button type={type} className={`hud-action ${className}`.trim()} onClick={onClick} disabled={disabled}
    aria-label={ariaLabel} title={title}>{children}</button>;
}
