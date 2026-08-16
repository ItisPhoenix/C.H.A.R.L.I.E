import type { ReactElement, ReactNode } from "react";

export interface ContextCardHeaderProps {
  title: string;
  subtitle?: string;
  category?: string;
  icon?: ReactNode | string;
  badge?: string;
  badgeVariant?: "cyan" | "emerald" | "amber" | "rose" | "slate";
  timestamp?: string;
  onClose?: () => void;
  className?: string;
}

export function ContextCardHeader({
  title,
  subtitle,
  category,
  icon,
  badge,
  badgeVariant = "cyan",
  timestamp,
  onClose,
  className = "",
}: ContextCardHeaderProps): ReactElement {
  const getBadgeStyle = () => {
    switch (badgeVariant) {
      case "emerald":
        return "text-emerald-300 bg-emerald-950/70 border-emerald-500/40";
      case "amber":
        return "text-amber-300 bg-amber-950/70 border-amber-500/40";
      case "rose":
        return "text-rose-300 bg-rose-950/70 border-rose-500/40";
      case "slate":
        return "text-slate-400 bg-slate-900/70 border-slate-700/40";
      case "cyan":
      default:
        return "text-cyan-300 bg-cyan-950/70 border-cyan-500/40";
    }
  };

  return (
    <div className={`charlie-card-header ${className}`}>
      <div className="flex items-start gap-2.5 min-w-0 flex-1">
        {icon && (
          <div className="flex-shrink-0 flex items-center justify-center w-6 h-6 rounded-md bg-cyan-950/60 border border-cyan-500/30 text-cyan-300 text-xs">
            {icon}
          </div>
        )}
        <div className="min-w-0 flex-1">
          {category && (
            <div className="text-[10px] text-cyan-400/80 font-bold uppercase tracking-wider mb-0.5 truncate">
              {category}
            </div>
          )}
          <h3 className="text-xs sm:text-sm font-bold text-slate-100 uppercase tracking-tight font-sans truncate">
            {title}
          </h3>
          {subtitle && (
            <div className="text-[11px] text-slate-400 tracking-normal font-sans mt-0.5 line-clamp-1">
              {subtitle}
            </div>
          )}
        </div>
      </div>

      <div className="flex items-center gap-2 flex-shrink-0">
        {badge && (
          <span
            className={`px-1.5 py-0.5 text-[9px] font-mono font-bold uppercase rounded border ${getBadgeStyle()}`}
          >
            {badge}
          </span>
        )}
        {timestamp && (
          <span className="text-[9.5px] font-mono text-slate-400">{timestamp}</span>
        )}
        {onClose && (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              onClose();
            }}
            className="w-5 h-5 flex items-center justify-center rounded text-slate-400 hover:text-cyan-300 hover:bg-slate-800/60 transition cursor-pointer text-xs font-mono"
            aria-label="Close"
          >
            ✕
          </button>
        )}
      </div>
    </div>
  );
}
