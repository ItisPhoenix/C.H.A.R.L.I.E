import type { ReactElement } from "react";
import type { PrimitiveSpec } from "../surfaceSchema";

export interface TimelineItem {
  time: string;
  title: string;
  summary?: string;
  status?: "completed" | "active" | "pending" | "warning";
}

export interface TimelineData {
  title?: string;
  layout?: "horizontal" | "vertical";
  items?: TimelineItem[];
}

export function TimelinePrimitive({
  primitive,
  data,
}: {
  primitive?: PrimitiveSpec;
  data?: TimelineData;
}): ReactElement {
  const tData: TimelineData = data || primitive?.data || {};
  const layout = tData.layout || "horizontal";
  const title = tData.title || "TIMELINE";
  const items = (Array.isArray(tData.items) ? tData.items : []) as TimelineItem[];

  if (!items.length) {
    return <div className="text-xs text-slate-500 italic my-2 font-mono">No timeline entries recorded.</div>;
  }

  // Horizontal Timeline Mode (Image 1 style)
  if (layout === "horizontal") {
    return (
      <div className="w-full font-mono select-none flex flex-col gap-2">
        {title && (
          <div className="text-left text-xs font-semibold text-cyan-200 tracking-wider uppercase">
            {title}
          </div>
        )}
        <div className="relative w-full pt-4 pb-2 px-2">
          {/* Background connecting horizontal line */}
          <div className="absolute top-[22px] left-4 right-4 h-[1px] bg-cyan-500/25" />

          {/* Milestone points */}
          <div className="relative flex justify-between items-start w-full">
            {items.map((item, idx) => {
              const isActive = item.status === "active" || idx === items.length - 1;
              const isWarning = item.status === "warning";
              const dotColor = isWarning
                ? "bg-amber-400 ring-4 ring-amber-400/20"
                : isActive
                  ? "bg-cyan-300 ring-4 ring-cyan-400/30 animate-pulse"
                  : "bg-cyan-600";

              return (
                <div key={idx} className="flex flex-col items-center text-center max-w-[130px] group">
                  {/* Glowing Node Dot */}
                  <span className={`w-2.5 h-2.5 rounded-full ${dotColor} mb-2 shadow-lg`} />

                  {/* Time Badge */}
                  <span className="text-[10px] font-bold text-cyan-300 tracking-wider">
                    {item.time}
                  </span>

                  {/* Title */}
                  <span className="text-[10px] text-slate-300 font-medium tracking-tight mt-0.5 uppercase leading-tight line-clamp-2">
                    {item.title}
                  </span>

                  {/* Summary if any */}
                  {item.summary && (
                    <span className="text-[9px] text-slate-400 mt-0.5 line-clamp-2">
                      {item.summary}
                    </span>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      </div>
    );
  }

  // Vertical Timeline Mode (Image 3 style)
  return (
    <div className="w-full font-mono select-none flex flex-col gap-2 text-left">
      {title && (
        <div className="text-xs font-semibold text-cyan-200 tracking-wider uppercase mb-1">
          {title}
        </div>
      )}
      <div className="relative pl-4 border-l border-cyan-500/25 flex flex-col gap-3">
        {items.map((item, idx) => {
          const isActive = item.status === "active" || idx === items.length - 1;
          const isWarning = item.status === "warning";
          const dotColor = isWarning
            ? "bg-amber-400"
            : isActive
              ? "bg-cyan-300 ring-4 ring-cyan-400/25 animate-pulse"
              : "bg-cyan-600";

          return (
            <div key={idx} className="relative flex items-start gap-3">
              {/* Timeline Dot */}
              <span
                className={`absolute -left-[21px] top-1.5 w-2 h-2 rounded-full ${dotColor}`}
              />
              <span className="text-[10px] font-bold text-cyan-400 whitespace-nowrap pt-0.5">
                {item.time}
              </span>
              <div className="flex-1">
                <span className="text-xs font-medium text-slate-200 leading-snug">
                  {item.title}
                </span>
                {item.summary && (
                  <p className="text-[11px] text-slate-400 mt-0.5 leading-relaxed">
                    {item.summary}
                  </p>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
