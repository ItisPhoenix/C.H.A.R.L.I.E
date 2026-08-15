import type { ReactElement } from "react";
import type { PrimitiveSpec } from "../surfaceSchema";

export function ProgressPrimitive({ primitive }: { primitive: PrimitiveSpec }): ReactElement {
  const data = primitive.data || {};
  const value = typeof data.value === "number" ? Math.max(0, Math.min(100, data.value)) : null;
  const label = data.label ? String(data.label) : null;
  const statusText = data.statusText ? String(data.statusText) : null;
  const isIndeterminate = value === null;

  return (
    <div className="w-full my-1.5" role="progressbar" aria-valuenow={value ?? undefined} aria-valuemin={0} aria-valuemax={100}>
      {(label || statusText || value !== null) && (
        <div className="flex justify-between items-center text-[11px] font-mono mb-1">
          <span className="text-slate-300 truncate">{label || "Progress"}</span>
          <span className="text-cyan-400">
            {value !== null ? `${Math.round(value)}%` : statusText || "Processing..."}
          </span>
        </div>
      )}
      <div className="w-full h-1.5 rounded-full bg-slate-900 border border-cyan-500/20 overflow-hidden relative">
        {isIndeterminate ? (
          <div className="h-full w-1/3 rounded-full bg-cyan-400 animate-[indeterminate_1.5s_infinite_ease-in-out]" />
        ) : (
          <div
            className="h-full rounded-full bg-cyan-400 transition-all duration-300"
            style={{ width: `${value}%` }}
          />
        )}
      </div>
    </div>
  );
}
