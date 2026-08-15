import type { ReactElement } from "react";
import type { PrimitiveSpec } from "../surfaceSchema";

interface TimelineItem {
  time: string;
  title: string;
  summary?: string;
  status?: "completed" | "active" | "pending" | "warning";
}

export function TimelinePrimitive({ primitive }: { primitive: PrimitiveSpec }): ReactElement {
  const data = primitive.data || {};
  const items = (Array.isArray(data.items) ? data.items : []) as TimelineItem[];

  if (!items.length) {
    return <div className="text-xs text-slate-500 italic my-2">No timeline entries recorded.</div>;
  }

  return (
    <div className="relative pl-5 border-l border-cyan-500/30 my-3 flex flex-col gap-4">
      {items.map((item, idx) => {
        const dotColor =
          item.status === "active"
            ? "bg-cyan-400 ring-4 ring-cyan-400/20 animate-pulse"
            : item.status === "warning"
              ? "bg-amber-400"
              : item.status === "pending"
                ? "bg-slate-700"
                : "bg-cyan-600";

        return (
          <div key={idx} className="relative flex flex-col gap-0.5 text-left">
            {/* Timeline Dot */}
            <span
              className={`absolute -left-[26px] top-1 w-2.5 h-2.5 rounded-full ${dotColor}`}
            />
            <div className="flex items-center gap-2">
              <span className="font-mono text-[10px] text-cyan-400">{item.time}</span>
              <span className="text-xs font-semibold text-slate-200">{item.title}</span>
            </div>
            {item.summary && <p className="text-xs text-slate-300 leading-relaxed">{item.summary}</p>}
          </div>
        );
      })}
    </div>
  );
}
