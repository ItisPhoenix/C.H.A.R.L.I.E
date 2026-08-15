import type { ReactElement } from "react";
import type { PrimitiveSpec } from "../surfaceSchema";

export function TextPrimitive({ primitive }: { primitive: PrimitiveSpec }): ReactElement {
  const data = primitive.data || {};
  const text = String(data.text ?? "");
  const variant = String(data.variant ?? "body"); // heading, title, subtitle, body, caption, code
  const level = Number(data.level ?? 2);

  if (variant === "heading" || primitive.type === "heading") {
    if (level === 1) {
      return <h1 className="text-lg font-bold text-cyan-200 tracking-wide mb-1">{text}</h1>;
    }
    if (level === 3) {
      return <h3 className="text-xs font-semibold text-cyan-300 uppercase tracking-wider mb-1">{text}</h3>;
    }
    return <h2 className="text-sm font-semibold text-slate-100 tracking-wide mb-1">{text}</h2>;
  }

  if (variant === "code" || data.monospace) {
    return (
      <pre className="p-2.5 rounded bg-slate-950/80 border border-cyan-500/20 font-mono text-xs text-cyan-300 overflow-x-auto whitespace-pre-wrap">
        <code>{text}</code>
      </pre>
    );
  }

  if (variant === "caption" || variant === "secondary") {
    return <p className="text-[11px] text-slate-400 leading-normal">{text}</p>;
  }

  return <p className="text-xs text-cyan-100/90 leading-relaxed">{text}</p>;
}
