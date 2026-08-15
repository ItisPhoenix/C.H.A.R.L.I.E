import type { ReactElement } from "react";
import type { PresentationIntent } from "../store/charlie";

interface WidgetLayerProps {
  widgets: PresentationIntent[];
  onDismiss?: (id: string) => void;
}

export function WidgetLayer({ widgets, onDismiss }: WidgetLayerProps): ReactElement | null {
  if (!widgets.length) return null;

  return (
    <div className="charlie-widget-layer" role="region" aria-label="Contextual Widgets">
      <div className="charlie-widget-zone charlie-zone-top-right">
        {widgets.map((w) => (
          <div
            key={w.id}
            className="p-4 rounded-xl bg-slate-950/80 border border-cyan-500/25 backdrop-blur-md shadow-xl text-left min-w-[260px] max-w-[340px]"
          >
            <div className="flex items-center justify-between gap-2 mb-2">
              <span className="text-[11px] font-mono text-cyan-400 uppercase">
                {w.widgetType || "WIDGET"}
              </span>
              {onDismiss && (
                <button
                  type="button"
                  onClick={() => onDismiss(w.id)}
                  className="text-xs text-slate-400 hover:text-cyan-300 cursor-pointer"
                >
                  ✕
                </button>
              )}
            </div>
            <h4 className="text-xs font-semibold text-slate-200">{w.title}</h4>
            <p className="text-xs text-cyan-100/80 mt-1 leading-normal">{w.summary}</p>
          </div>
        ))}
      </div>
    </div>
  );
}
