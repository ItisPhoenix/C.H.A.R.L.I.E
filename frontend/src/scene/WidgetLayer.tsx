import { useEffect, type ReactElement } from "react";
import { WidgetContainer } from "../layout/WidgetContainer";
import { useWidgetStore } from "../layout/widgetStore";

export function WidgetLayer({ onDismiss }: { onDismiss?: (id: string) => void }): ReactElement | null {
  const widgets = useWidgetStore((s) => s.widgets);
  const focusWidget = useWidgetStore((s) => s.focusWidget);
  const dragWidget = useWidgetStore((s) => s.dragWidget);
  const resizeWidget = useWidgetStore((s) => s.resizeWidget);
  const pinWidget = useWidgetStore((s) => s.pinWidget);
  const unpinWidget = useWidgetStore((s) => s.unpinWidget);
  const pauseExpiry = useWidgetStore((s) => s.pauseExpiry);
  const resumeExpiry = useWidgetStore((s) => s.resumeExpiry);
  const dismissWidget = useWidgetStore((s) => s.dismissWidget);
  const tickAutoDismiss = useWidgetStore((s) => s.tickAutoDismiss);

  // Auto-dismiss countdown ticker: only runs interval when expiring widgets are present
  const hasExpiringWidgets = Object.values(widgets).some(
    (w) => !w.pinned && !w.pausedExpiry && Boolean(w.expiresAt)
  );

  useEffect(() => {
    if (!hasExpiringWidgets) return;
    const interval = setInterval(() => {
      tickAutoDismiss(Date.now());
    }, 500);
    return () => clearInterval(interval);
  }, [hasExpiringWidgets, tickAutoDismiss]);

  const widgetList = Object.values(widgets);
  if (!widgetList.length) return null;

  return (
    <div className="charlie-widget-layer" role="region" aria-label="Contextual Widgets">
      {widgetList.map((w) => (
        <WidgetContainer
          key={w.id}
          widget={w}
          onFocus={focusWidget}
          onDrag={(id, pos) =>
            dragWidget(id, pos, { width: window.innerWidth, height: window.innerHeight })
          }
          onResize={(id, size) =>
            resizeWidget(id, size, { width: window.innerWidth, height: window.innerHeight })
          }
          onPin={(id) =>
            pinWidget(id, { width: window.innerWidth, height: window.innerHeight })
          }
          onUnpin={unpinWidget}
          onPauseExpiry={pauseExpiry}
          onResumeExpiry={resumeExpiry}
          onDismiss={(id) => {
            dismissWidget(id);
            onDismiss?.(id);
          }}
        />
      ))}
    </div>
  );
}
