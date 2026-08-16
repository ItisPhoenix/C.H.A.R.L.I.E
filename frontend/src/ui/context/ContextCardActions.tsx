import type { ReactElement, ReactNode } from "react";

export interface CardActionItem {
  id: string;
  label: string;
  onClick: () => void;
  variant?: "primary" | "default" | "subtle" | "danger";
  icon?: ReactNode | string;
  disabled?: boolean;
}

export interface ContextCardActionsProps {
  actions?: CardActionItem[];
  children?: ReactNode;
  className?: string;
}

export function ContextCardActions({
  actions = [],
  children,
  className = "",
}: ContextCardActionsProps): ReactElement | null {
  if (actions.length === 0 && !children) return null;

  const getActionClass = (variant: CardActionItem["variant"] = "default") => {
    switch (variant) {
      case "primary":
        return "bg-cyan-500/20 text-cyan-200 border-cyan-400/50 hover:bg-cyan-500/35 hover:border-cyan-300";
      case "danger":
        return "bg-rose-500/20 text-rose-200 border-rose-400/50 hover:bg-rose-500/35 hover:border-rose-300";
      case "subtle":
        return "bg-transparent text-slate-400 border-transparent hover:text-slate-200 hover:bg-slate-800/40";
      case "default":
      default:
        return "bg-slate-900/60 text-slate-300 border-cyan-500/20 hover:border-cyan-400/40 hover:text-cyan-200 hover:bg-slate-800/60";
    }
  };

  return (
    <div className={`charlie-card-actions ${className}`}>
      {actions.map((act) => (
        <button
          key={act.id}
          type="button"
          disabled={act.disabled}
          onClick={(e) => {
            e.stopPropagation();
            act.onClick();
          }}
          className={`flex items-center gap-1.5 px-2.5 py-1 rounded-md text-[10.5px] font-mono font-medium border transition cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed ${getActionClass(
            act.variant
          )}`}
        >
          {act.icon && <span className="text-xs">{act.icon}</span>}
          <span>{act.label}</span>
        </button>
      ))}
      {children}
    </div>
  );
}
