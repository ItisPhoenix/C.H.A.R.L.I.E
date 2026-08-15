import type { ReactElement } from "react";
import type { PrimitiveSpec } from "../surfaceSchema";

export function StatusPrimitive({ primitive }: { primitive: PrimitiveSpec }): ReactElement {
  const data = primitive.data || {};
  const status = String(data.status ?? "info"); // success, info, warning, critical, unknown
  const text = String(data.text ?? status.toUpperCase());

  const config = {
    success: { bg: "bg-emerald-950/60 border-emerald-500/40 text-emerald-300", icon: "✓" },
    warning: { bg: "bg-amber-950/60 border-amber-500/40 text-amber-300", icon: "!" },
    critical: { bg: "bg-rose-950/60 border-rose-500/40 text-rose-300", icon: "✕" },
    unknown: { bg: "bg-slate-950/60 border-slate-500/40 text-slate-400", icon: "?" },
    info: { bg: "bg-cyan-950/60 border-cyan-500/40 text-cyan-300", icon: "ℹ" },
  }[status] || { bg: "bg-cyan-950/60 border-cyan-500/40 text-cyan-300", icon: "ℹ" };

  return (
    <div className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full border text-xs font-mono font-medium ${config.bg}`}>
      <span>{config.icon}</span>
      <span>{text}</span>
    </div>
  );
}

export function BadgePrimitive({ primitive }: { primitive: PrimitiveSpec }): ReactElement {
  const data = primitive.data || {};
  const text = String(data.text ?? "");
  return (
    <span className="inline-block px-2 py-0.5 rounded font-mono text-[10px] uppercase font-semibold bg-cyan-950/80 text-cyan-300 border border-cyan-500/30">
      {text}
    </span>
  );
}

export function DividerPrimitive(): ReactElement {
  return <hr className="w-full border-t border-cyan-500/15 my-2" />;
}
