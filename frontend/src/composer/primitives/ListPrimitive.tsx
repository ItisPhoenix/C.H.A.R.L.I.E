import type { ReactElement } from "react";
import type { PrimitiveSpec } from "../surfaceSchema";

interface ListItem {
  text: string;
  label?: string;
  tag?: string;
  status?: string;
}

export function ListPrimitive({ primitive }: { primitive: PrimitiveSpec }): ReactElement {
  const data = primitive.data || {};
  const items = (Array.isArray(data.items) ? data.items : []) as ListItem[];
  const style = String(data.style ?? "unordered"); // unordered, ordered, ranked, compact

  if (style === "ordered" || style === "ranked") {
    return (
      <ol className="flex flex-col gap-1.5 list-none my-1">
        {items.map((item, idx) => (
          <li
            key={idx}
            className="flex items-start gap-2.5 p-2 rounded-lg bg-slate-950/40 border border-cyan-500/10 text-xs"
          >
            <span className="font-mono text-[11px] text-cyan-400 font-bold w-4 shrink-0 text-right">
              {idx + 1}.
            </span>
            <div className="flex-1 overflow-hidden">
              {item.label && <div className="font-semibold text-slate-200">{item.label}</div>}
              <div className="text-cyan-100/90 leading-relaxed">{item.text}</div>
            </div>
            {item.tag && (
              <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-cyan-950/80 text-cyan-300 border border-cyan-500/30 shrink-0">
                {item.tag}
              </span>
            )}
          </li>
        ))}
      </ol>
    );
  }

  return (
    <ul className="flex flex-col gap-1.5 list-none my-1">
      {items.map((item, idx) => (
        <li
          key={idx}
          className="flex items-start gap-2 p-1.5 rounded text-xs text-cyan-100/90"
        >
          <span className="text-cyan-400 font-bold shrink-0">•</span>
          <div className="flex-1 overflow-hidden">
            {item.label && <span className="font-semibold text-slate-200 mr-1">{item.label}:</span>}
            <span>{item.text}</span>
          </div>
          {item.tag && (
            <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-cyan-950/80 text-cyan-300 border border-cyan-500/30 shrink-0">
              {item.tag}
            </span>
          )}
        </li>
      ))}
    </ul>
  );
}
