import { create } from "zustand";
import type { LayoutProfile } from "./layoutProfile";

export interface PanelLayout {
  x: number;
  y: number;
  w: number;
  h: number;
  z: number;
  minimized: boolean;
  open: boolean;
}

interface LayoutState {
  panels: Record<string, PanelLayout>;
  layouts: Record<LayoutProfile, Record<string, PanelLayout>>;
  profile: LayoutProfile;
  topZ: number;
  setProfile: (profile: LayoutProfile) => void;
  open: (id: string) => void;
  close: (id: string) => void;
  toggleMinimize: (id: string) => void;
  move: (id: string, x: number, y: number) => void;
  resize: (id: string, w: number, h: number) => void;
  focus: (id: string) => void;
  resetPosition: (id: string) => void;
  resetAll: () => void;
}

const DEFAULT_LAYOUT: Record<string, PanelLayout> = {
  chat: { x: 72, y: 98, w: 360, h: 316, z: 5, minimized: false, open: true },
  tasks: { x: 1037, y: 82, w: 337, h: 288, z: 3, minimized: false, open: true },
  system: { x: 1140, y: 386, w: 358, h: 195, z: 4, minimized: false, open: true },
  tools: { x: 35, y: 412, w: 338, h: 262, z: 3, minimized: false, open: false },
  terminal: { x: 119, y: 689, w: 307, h: 196, z: 4, minimized: false, open: false },
  mcp: { x: 61, y: 899, w: 365, h: 109, z: 4, minimized: false, open: false },
  media: { x: 1019, y: 600, w: 340, h: 180, z: 4, minimized: false, open: false },
  calendar: { x: 1061, y: 796, w: 353, h: 213, z: 4, minimized: false, open: false },
  settings: { x: 460, y: 120, w: 616, h: 720, z: 8, minimized: false, open: false },
};

const HUD_WIDTH = 1536;
const HUD_HEIGHT = 1024;
const MIN_PANEL_WIDTH = 280;
const MIN_PANEL_HEIGHT = 160;
const LAYOUT_STORAGE_KEY = "charlie.dashboard.layouts.v1";

function freshDefaultLayout(): Record<string, PanelLayout> {
  return Object.fromEntries(Object.entries(DEFAULT_LAYOUT).map(([id, layout]) => [id, { ...layout }]));
}

function freshProfileLayouts(): Record<LayoutProfile, Record<string, PanelLayout>> {
  return { compact: freshDefaultLayout(), laptop: freshDefaultLayout(), desktop: freshDefaultLayout() };
}

function storedProfileLayouts(): Record<LayoutProfile, Record<string, PanelLayout>> {
  if (typeof window === "undefined") return freshProfileLayouts();
  try {
    const saved = JSON.parse(window.localStorage.getItem(LAYOUT_STORAGE_KEY) ?? "null") as Record<LayoutProfile, Record<string, PanelLayout>> | null;
    if (saved?.compact && saved.laptop && saved.desktop) return saved;
  } catch {
    // Corrupt browser storage should never prevent Charlie from opening.
  }
  return freshProfileLayouts();
}

function withActiveLayout(state: LayoutState, panels: Record<string, PanelLayout>): Pick<LayoutState, "panels" | "layouts"> {
  return { panels, layouts: { ...state.layouts, [state.profile]: panels } };
}

const INITIAL_LAYOUTS = storedProfileLayouts();

export const useLayoutStore = create<LayoutState>((set) => ({
  panels: INITIAL_LAYOUTS.desktop,
  layouts: INITIAL_LAYOUTS,
  profile: "desktop",
  topZ: 10,
  setProfile: (profile) => set((state) => ({ profile, panels: state.layouts[profile] })),
  open: (id) => set((state) => {
    const panel = state.panels[id];
    if (!panel) return state;
    const topZ = state.topZ + 1;
    return { topZ, ...withActiveLayout(state, { ...state.panels, [id]: { ...panel, open: true, minimized: false, z: topZ } }) };
  }),
  close: (id) => set((state) => withActiveLayout(state, { ...state.panels, [id]: { ...state.panels[id], open: false } })),
  toggleMinimize: (id) => set((state) => withActiveLayout(state, { ...state.panels, [id]: { ...state.panels[id], minimized: !state.panels[id].minimized } })),
  move: (id, x, y) => set((state) => ({
    ...withActiveLayout(state, {
      ...state.panels,
      [id]: { ...state.panels[id], x: Math.max(0, Math.min(HUD_WIDTH - state.panels[id].w, x)), y: Math.max(68, Math.min(HUD_HEIGHT - 42, y)) },
    }),
  })),
  resize: (id, w, h) => set((state) => {
    const panel = state.panels[id];
    if (!panel) return state;
    return withActiveLayout(state, {
        ...state.panels,
        [id]: {
          ...panel,
          w: Math.max(MIN_PANEL_WIDTH, Math.min(HUD_WIDTH - panel.x, w)),
          h: Math.max(MIN_PANEL_HEIGHT, Math.min(HUD_HEIGHT - panel.y, h)),
        },
      });
  }),
  focus: (id) => set((state) => {
    const topZ = state.topZ + 1;
    return { topZ, ...withActiveLayout(state, { ...state.panels, [id]: { ...state.panels[id], z: topZ } }) };
  }),
  resetPosition: (id) => set((state) => withActiveLayout(state, { ...state.panels, [id]: { ...state.panels[id], x: DEFAULT_LAYOUT[id].x, y: DEFAULT_LAYOUT[id].y } })),
  resetAll: () => {
    const layouts = freshProfileLayouts();
    set({ panels: layouts.desktop, layouts, profile: "desktop", topZ: 10 });
  },
}));

if (typeof window !== "undefined") {
  useLayoutStore.subscribe((state) => {
    window.localStorage.setItem(LAYOUT_STORAGE_KEY, JSON.stringify(state.layouts));
  });
}
