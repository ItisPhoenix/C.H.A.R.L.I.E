import type { ReactElement } from "react";
import type { WidgetDefinition } from "../../presentation/presentationRegistry";
import type { WidgetInstance } from "../../layout/widgetStore";

export function GenericWidget({ widget, definition }: { widget: WidgetInstance; definition: WidgetDefinition | null }): ReactElement {
  return (
    <div className="flex h-full flex-col justify-center text-left">
      <div className="mb-1 text-[10px] font-mono uppercase tracking-widest text-cyan-400/80">
        {definition?.name ?? widget.widgetType}
      </div>
      <h4 className="mb-1 text-xs font-semibold text-slate-200">{widget.title}</h4>
      <p className="text-xs leading-relaxed text-cyan-100/85">{widget.summary || definition?.description || "No widget data available."}</p>
    </div>
  );
}
