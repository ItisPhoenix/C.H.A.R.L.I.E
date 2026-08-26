import { create } from "zustand";
import type { PresentationIntent } from "../store/charlie";
import {
  clampToViewport,
  findCollisionFreePosition,
  fromNormalizedRect,
  toNormalizedRect,
  type NormalizedRect,
  type Point,
  type Rect,
  type Size,
} from "./geometry";
import {
  DEFAULT_WIDGET_SIZE,
  getZoneCandidateRect,
  MAX_WIDGET_SIZE,
  MIN_WIDGET_SIZE,
  type LayoutZone,
  type ZoneContext,
} from "./zones";
import { getWidgetDefinition, resolveWidgetType } from "../presentation/presentationRegistry";

export interface WidgetInstance {
  id: string;
  presentationIntentId: string;
  widgetType: string;
  taskId: string | null;
  title: string;
  summary: string;
  content: Record<string, unknown>;
  position: Point;
  size: Size;
  zone: LayoutZone;
  focused: boolean;
  minimized: boolean;
  pinned: boolean;
  autoDismissMs: number | null;
  /** TTL captured before pinning so unpin restores the widget contract. */
  restoreAutoDismissMs?: number | null;
  expiresAt: number | null;
  pausedExpiry: boolean;
  createdAt: string;
  updatedAt: string;
  replaceKey: string | null;
  zIndex: number;
}

const PINNED_STORAGE_KEY = "charlie_pinned_widgets";

function loadPinnedLayouts(): Record<string, NormalizedRect> {
  try {
    const raw = localStorage.getItem(PINNED_STORAGE_KEY);
    return raw ? JSON.parse(raw) : {};
  } catch {
    return {};
  }
}

function savePinnedLayouts(layouts: Record<string, NormalizedRect>) {
  try {
    localStorage.setItem(PINNED_STORAGE_KEY, JSON.stringify(layouts));
  } catch {
    // Ignore localStorage errors in private / restricted mode
  }
}

interface WidgetStoreState {
  widgets: Record<string, WidgetInstance>;
  topZIndex: number;
  focusedWidgetId: string | null;
  pinnedLayouts: Record<string, NormalizedRect>;

  // Actions
  upsertWidget: (intent: PresentationIntent, ctx: ZoneContext) => WidgetInstance;
  focusWidget: (id: string) => void;
  dragWidget: (id: string, newPos: Point, viewport: Size) => void;
  resizeWidget: (id: string, newSize: Size, viewport: Size) => void;
  pinWidget: (id: string, viewport: Size) => void;
  unpinWidget: (id: string) => void;
  pauseExpiry: (id: string) => void;
  resumeExpiry: (id: string) => void;
  dismissWidget: (id: string) => void;
  tickAutoDismiss: (now?: number) => void;
  clearScreen: () => void;
  clearEverything: () => void;
  restoreEverything: () => void;
  focusedEscape: () => boolean;
}

export const useWidgetStore = create<WidgetStoreState>((set, get) => ({
  widgets: {},
  topZIndex: 10,
  focusedWidgetId: null,
  pinnedLayouts: loadPinnedLayouts(),

  upsertWidget: (intent: PresentationIntent, ctx: ZoneContext) => {
    const now = Date.now();
    const isoNow = new Date(now).toISOString();
    const currentWidgets = get().widgets;

    // Check for matching replaceKey or same ID
    let existingId: string | null = null;
    if (intent.id in currentWidgets) {
      existingId = intent.id;
    } else if (intent.replaceKey) {
      const match = Object.values(currentWidgets).find((w) => w.replaceKey === intent.replaceKey);
      if (match) existingId = match.id;
    }

    const nextZ = get().topZIndex + 1;
    const zone = (intent.preferredZone as LayoutZone) || "contextual";
    const canonicalWidgetType = resolveWidgetType(intent.widgetType) ?? intent.widgetType ?? "unknown";

    if (existingId && currentWidgets[existingId]) {
      const existing = currentWidgets[existingId];
      if (
        existingId === intent.id &&
        existing.summary === (intent.summary || existing.summary) &&
        existing.title === (intent.title || existing.title)
      ) {
        return existing;
      }
      // Update in place, preserving position, size, pin state, and paused state
      const autoMs = intent.autoDismissMs ?? existing.autoDismissMs;
      const expiresAt = existing.pinned
        ? null
        : autoMs
          ? now + autoMs
          : null;

      const updated: WidgetInstance = {
        ...existing,
        id: intent.id, // Update to new intent ID
        presentationIntentId: intent.id,
        taskId: intent.taskId ?? existing.taskId,
        title: intent.title || existing.title,
        summary: intent.summary || existing.summary,
        content: intent.content || existing.content,
        autoDismissMs: autoMs,
        expiresAt,
        updatedAt: isoNow,
        zIndex: nextZ,
      };

      const next = { ...currentWidgets };
      if (existingId !== intent.id) {
        delete next[existingId];
      }
      next[intent.id] = updated;

      set({ widgets: next, topZIndex: nextZ, focusedWidgetId: intent.id });
      return updated;
    }

    // New widget: Calculate placement
    const candidateSize: Size =
      canonicalWidgetType === "system_metric"
        ? { width: 320, height: 190 }
        : DEFAULT_WIDGET_SIZE;

    // 1. Check if we have a saved pinned layout for this widgetType / replaceKey
    const pinKey = intent.replaceKey || intent.widgetType || intent.id;
    const savedPin = get().pinnedLayouts[pinKey];

    let position: Point;
    let size: Size = candidateSize;
    let isPinned = false;

    if (savedPin) {
      const restored = fromNormalizedRect(savedPin, ctx.viewport);
      position = { x: restored.x, y: restored.y };
      size = { width: restored.width, height: restored.height };
      isPinned = true;
    } else {
      const candidateRect = getZoneCandidateRect(zone, candidateSize, ctx);

      // Collect obstacles: core bounds, primary workspace, and existing visible widgets
      const obstacles: Rect[] = [ctx.coreBounds];
      if (ctx.workspaceBounds) {
        obstacles.push(ctx.workspaceBounds);
      }
      for (const w of Object.values(currentWidgets)) {
        if (!w.minimized) {
          obstacles.push({ x: w.position.x, y: w.position.y, width: w.size.width, height: w.size.height });
        }
      }

      const freeRect = findCollisionFreePosition(candidateRect, obstacles, ctx.viewport, ctx.safeMargin);
      position = { x: freeRect.x, y: freeRect.y };
      size = { width: freeRect.width, height: freeRect.height };
    }

    const registryAutoMs = getWidgetDefinition(canonicalWidgetType)?.default_auto_dismiss_ms ?? null;
    const autoMs = isPinned ? null : intent.autoDismissMs ?? registryAutoMs;
    const expiresAt = autoMs ? now + autoMs : null;

    const newWidget: WidgetInstance = {
      id: intent.id,
      presentationIntentId: intent.id,
      widgetType: canonicalWidgetType || (intent as any).widget_type || "unknown",
      taskId: intent.taskId ?? null,
      title: intent.title || "WIDGET",
      summary: intent.summary || "",
      content: (intent.content || (intent as any).contentState || {}) as Record<string, unknown>,
      position,
      size,
      zone,
      focused: true,
      minimized: false,
      pinned: isPinned,
      autoDismissMs: autoMs,
      expiresAt,
      pausedExpiry: false,
      createdAt: isoNow,
      updatedAt: isoNow,
      replaceKey: intent.replaceKey ?? null,
      zIndex: nextZ,
    };

    set((state) => ({
      widgets: { ...state.widgets, [intent.id]: newWidget },
      topZIndex: nextZ,
      focusedWidgetId: intent.id,
    }));

    return newWidget;
  },

  focusWidget: (id: string) => {
    const w = get().widgets[id];
    if (!w) return;
    const nextZ = get().topZIndex + 1;
    set((state) => ({
      widgets: {
        ...state.widgets,
        [id]: { ...w, focused: true, zIndex: nextZ },
      },
      topZIndex: nextZ,
      focusedWidgetId: id,
    }));
  },

  dragWidget: (id: string, newPos: Point, viewport: Size) => {
    const w = get().widgets[id];
    if (!w) return;
    const clamped = clampToViewport({ x: newPos.x, y: newPos.y, width: w.size.width, height: w.size.height }, viewport);

    set((state) => ({
      widgets: {
        ...state.widgets,
        [id]: {
          ...w,
          position: { x: clamped.x, y: clamped.y },
          focused: true,
          updatedAt: new Date().toISOString(),
        },
      },
      focusedWidgetId: id,
    }));

    // If pinned, update stored layout
    if (w.pinned) {
      const pinKey = w.replaceKey || w.widgetType || w.id;
      const normalized = toNormalizedRect({ x: clamped.x, y: clamped.y, width: w.size.width, height: w.size.height }, viewport);
      const nextLayouts = { ...get().pinnedLayouts, [pinKey]: normalized };
      savePinnedLayouts(nextLayouts);
      set({ pinnedLayouts: nextLayouts });
    }
  },

  resizeWidget: (id: string, newSize: Size, viewport: Size) => {
    const w = get().widgets[id];
    if (!w) return;

    const clampedW = Math.min(Math.max(newSize.width, MIN_WIDGET_SIZE.width), MAX_WIDGET_SIZE.width);
    const clampedH = Math.min(Math.max(newSize.height, MIN_WIDGET_SIZE.height), MAX_WIDGET_SIZE.height);
    const clamped = clampToViewport({ x: w.position.x, y: w.position.y, width: clampedW, height: clampedH }, viewport);

    set((state) => ({
      widgets: {
        ...state.widgets,
        [id]: {
          ...w,
          position: { x: clamped.x, y: clamped.y },
          size: { width: clamped.width, height: clamped.height },
          focused: true,
          updatedAt: new Date().toISOString(),
        },
      },
      focusedWidgetId: id,
    }));

    if (w.pinned) {
      const pinKey = w.replaceKey || w.widgetType || w.id;
      const normalized = toNormalizedRect(clamped, viewport);
      const nextLayouts = { ...get().pinnedLayouts, [pinKey]: normalized };
      savePinnedLayouts(nextLayouts);
      set({ pinnedLayouts: nextLayouts });
    }
  },

  pinWidget: (id: string, viewport: Size) => {
    const w = get().widgets[id];
    if (!w) return;

    const pinKey = w.replaceKey || w.widgetType || w.id;
    const restoreAutoDismissMs = w.autoDismissMs ?? getWidgetDefinition(w.widgetType)?.default_auto_dismiss_ms ?? null;
    const normalized = toNormalizedRect({ x: w.position.x, y: w.position.y, width: w.size.width, height: w.size.height }, viewport);
    const nextLayouts = { ...get().pinnedLayouts, [pinKey]: normalized };
    savePinnedLayouts(nextLayouts);

    set((state) => ({
      widgets: {
        ...state.widgets,
        [id]: { ...w, pinned: true, autoDismissMs: null, restoreAutoDismissMs, expiresAt: null },
      },
      pinnedLayouts: nextLayouts,
    }));
  },

  unpinWidget: (id: string) => {
    const w = get().widgets[id];
    if (!w) return;

    const pinKey = w.replaceKey || w.widgetType || w.id;
    const nextLayouts = { ...get().pinnedLayouts };
    delete nextLayouts[pinKey];
    savePinnedLayouts(nextLayouts);

    const autoMs = Object.prototype.hasOwnProperty.call(w, "restoreAutoDismissMs")
      ? (w.restoreAutoDismissMs ?? null)
      : (getWidgetDefinition(w.widgetType)?.default_auto_dismiss_ms ?? null);
    set((state) => ({
      widgets: {
        ...state.widgets,
        [id]: {
          ...w,
          pinned: false,
          autoDismissMs: autoMs,
          restoreAutoDismissMs: undefined,
          expiresAt: autoMs ? Date.now() + autoMs : null,
        },
      },
      pinnedLayouts: nextLayouts,
    }));
  },

  pauseExpiry: (id: string) => {
    const w = get().widgets[id];
    if (!w || w.pinned) return;
    set((state) => ({
      widgets: { ...state.widgets, [id]: { ...w, pausedExpiry: true } },
    }));
  },

  resumeExpiry: (id: string) => {
    const w = get().widgets[id];
    if (!w || w.pinned) return;
    const autoMs = w.autoDismissMs || 5000;
    set((state) => ({
      widgets: { ...state.widgets, [id]: { ...w, pausedExpiry: false, expiresAt: Date.now() + autoMs } },
    }));
  },

  dismissWidget: (id: string) => {
    set((state) => {
      const next = { ...state.widgets };
      delete next[id];
      return {
        widgets: next,
        focusedWidgetId: state.focusedWidgetId === id ? null : state.focusedWidgetId,
      };
    });
  },

  tickAutoDismiss: (now = Date.now()) => {
    const current = get().widgets;
    let changed = false;
    const next: Record<string, WidgetInstance> = {};

    for (const [id, w] of Object.entries(current)) {
      if (!w.pinned && !w.pausedExpiry && w.expiresAt && now >= w.expiresAt) {
        changed = true;
      } else {
        next[id] = w;
      }
    }

    if (changed) {
      set({ widgets: next });
    }
  },

  clearScreen: () => {
    // Dismiss all temporary (unpinned) widgets, preserving pinned widgets
    set((state) => {
      const next: Record<string, WidgetInstance> = {};
      for (const [id, w] of Object.entries(state.widgets)) {
        if (w.pinned) {
          next[id] = w;
        }
      }
      return { widgets: next, focusedWidgetId: null };
    });
  },

  clearEverything: () => {
    // Temporarily hide all widgets including pinned (without unpinning)
    set((state) => {
      const next: Record<string, WidgetInstance> = {};
      for (const [id, w] of Object.entries(state.widgets)) {
        next[id] = { ...w, minimized: true };
      }
      return { widgets: next, focusedWidgetId: null };
    });
  },

  restoreEverything: () => {
    // Restore all minimized widgets
    set((state) => {
      const next: Record<string, WidgetInstance> = {};
      for (const [id, w] of Object.entries(state.widgets)) {
        next[id] = { ...w, minimized: false };
      }
      return { widgets: next };
    });
  },

  focusedEscape: () => {
    const focusedId = get().focusedWidgetId;
    if (focusedId && get().widgets[focusedId]) {
      get().dismissWidget(focusedId);
      return true;
    }
    return false;
  },
}));
