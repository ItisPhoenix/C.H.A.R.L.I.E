import type { ReactElement } from "react";
import type { WidgetInstance } from "../../layout/widgetStore";

export function UnavailableWidget({ widget }: { widget: WidgetInstance }): ReactElement {
  return (
    <div className="flex h-full flex-col justify-center text-left" data-widget-unavailable="true">
      <div className="mb-1 text-[10px] font-mono uppercase tracking-widest text-amber-300">WIDGET UNAVAILABLE</div>
      <h4 className="mb-1 text-xs font-semibold text-slate-200">{widget.title || "Unknown widget"}</h4>
      <p className="text-xs leading-relaxed text-amber-100/85">
        No renderer registered for <span className="font-mono">{widget.widgetType}</span>.
      </p>
    </div>
  );
}
