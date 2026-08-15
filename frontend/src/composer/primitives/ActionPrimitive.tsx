import type { ReactElement } from "react";
import type { ActionSpec, PrimitiveSpec } from "../surfaceSchema";

interface ActionPrimitiveProps {
  primitive?: PrimitiveSpec;
  action?: ActionSpec;
  onAction?: (action: ActionSpec) => void;
}

export function ActionPrimitive({ primitive, action, onAction }: ActionPrimitiveProps): ReactElement | null {
  const act: ActionSpec =
    action ||
    (primitive
      ? {
          id: primitive.id || "action",
          label: String(primitive.data?.label ?? "Action"),
          action_id: String(primitive.data?.action_id ?? "unknown"),
          payload: (primitive.data?.payload as Record<string, unknown>) ?? {},
          variant: (primitive.data?.variant as ActionSpec["variant"]) ?? "default",
          disabled: Boolean(primitive.data?.disabled),
        }
      : { id: "none", label: "Action", action_id: "none" });

  const variantStyles = {
    primary: "bg-cyan-500/20 text-cyan-200 border-cyan-400/50 hover:bg-cyan-500/30",
    danger: "bg-rose-950/60 text-rose-200 border-rose-500/40 hover:bg-rose-900/60",
    subtle: "bg-transparent text-slate-400 border-transparent hover:text-cyan-300",
    default: "bg-slate-900/80 text-cyan-300 border-cyan-500/30 hover:bg-cyan-950/80 hover:border-cyan-500/50",
  }[act.variant || "default"];

  return (
    <button
      type="button"
      disabled={act.disabled}
      onClick={(e) => {
        e.stopPropagation();
        if (onAction && !act.disabled) {
          onAction(act);
        }
      }}
      className={`px-3 py-1.5 rounded-lg border text-xs font-medium font-mono transition-all cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed ${variantStyles}`}
    >
      {act.label}
    </button>
  );
}
