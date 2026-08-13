import { create } from "zustand";

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
  topZ: number;
  open: (id: string) => void;
  close: (id: string) => void;
  toggleMinimize: (id: string) => void;
  move: (id: string, x: number, y: number) => void;
  focus: (id: string) => void;
  resetPosition: (id: string) => void;
}

const DEFAULT_LAYOUT: Record<string, PanelLayout> = {
  tasks: { x: 1037, y: 82, w: 337, h: 288, z: 3, minimized: false, open: true },
  system: { x: 1140, y: 386, w: 358, h: 195, z: 4, minimized: false, open: true },
  tools: { x: 35, y: 412, w: 338, h: 262, z: 3, minimized: false, open: true },
  terminal: { x: 119, y: 689, w: 307, h: 196, z: 4, minimized: false, open: true },
  mcp: { x: 61, y: 899, w: 365, h: 109, z: 4, minimized: false, open: true },
  media: { x: 1019, y: 600, w: 340, h: 180, z: 4, minimized: false, open: true },
  calendar: { x: 1061, y: 796, w: 353, h: 213, z: 4, minimized: false, open: true },
};

export const useLayoutStore = create<LayoutState>((set) => ({
  panels: DEFAULT_LAYOUT,
  topZ: 10,
  open: (id) => set((state) => {
    const panel = state.panels[id];
    if (!panel) return state;
    const topZ = state.topZ + 1;
    return { topZ, panels: { ...state.panels, [id]: { ...panel, open: true, minimized: false, z: topZ } } };
  }),
  close: (id) => set((state) => ({ panels: { ...state.panels, [id]: { ...state.panels[id], open: false } } })),
  toggleMinimize: (id) => set((state) => ({ panels: { ...state.panels, [id]: { ...state.panels[id], minimized: !state.panels[id].minimized } } })),
  move: (id, x, y) => set((state) => ({
    panels: {
      ...state.panels,
      [id]: { ...state.panels[id], x: Math.max(0, Math.min(1536 - state.panels[id].w, x)), y: Math.max(68, Math.min(1024 - 42, y)) },
    },
  })),
  focus: (id) => set((state) => {
    const topZ = state.topZ + 1;
    return { topZ, panels: { ...state.panels, [id]: { ...state.panels[id], z: topZ } } };
  }),
  resetPosition: (id) => set((state) => ({
    panels: { ...state.panels, [id]: { ...state.panels[id], x: DEFAULT_LAYOUT[id].x, y: DEFAULT_LAYOUT[id].y } },
  })),
}));
