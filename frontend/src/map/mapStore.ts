import { create } from "zustand";
import type {
  MapCameraState,
  MapCommand,
  MapFeature,
  MapRoute,
  ProviderMode,
  QualityTier,
  LayerStatus,
  LayerMetadata,
} from "./types";

export interface MapStoreState extends MapCameraState {
  // Mode & Quality
  providerMode: ProviderMode;
  quality: QualityTier;
  isReady: boolean;
  terrain3D: boolean;

  // Active Tile & Style URLs
  pmtilesUrl: string | null;
  pmtilesTileType: string;
  pmtilesMetadata: any | null;
  availableArchives: Array<{ name: string; url: string; valid: boolean; tileType: string; minZoom: number; maxZoom: number; metadata?: any }>;
  onlineSourceUrl: string;
  customStyleUrl: string | null;
  customVectorSourceUrl: string | null;

  // Layer States (Mandatory: Every layer default is strictly false)
  activeLayers: Record<string, boolean>;
  layerStatus: Record<string, { status: LayerStatus; error?: string }>;
  layerMetadata: Record<string, LayerMetadata>;
  layerData: Record<string, MapFeature[]>;

  // Selection & Route
  selectedFeature: MapFeature | null;
  route: MapRoute | null;

  // Command Execution Queue
  pendingCommand: { command: MapCommand; revision: number } | null;
  commandRevision: number;

  // User Interaction Tracking (Prevents fighting user control)
  userInteracting: boolean;
  lastUserInteractionTimestamp: number;

  // Actions
  setCamera: (camera: Partial<MapCameraState>) => void;
  setReady: (ready: boolean) => void;
  setProviderMode: (mode: ProviderMode) => void;
  setQuality: (quality: QualityTier) => void;
  setPmtilesUrl: (url: string | null, tileType?: string, metadata?: any) => void;
  fetchAvailableArchives: () => Promise<void>;
  setOnlineSourceUrl: (url: string) => void;
  setCustomStyleUrl: (url: string | null) => void;
  setCustomVectorSourceUrl: (url: string | null) => void;
  setTerrain3D: (enabled: boolean) => void;

  setLayerEnabled: (layerId: string, enabled: boolean) => void;
  toggleLayer: (layerId: string) => void;
  setLayerStatus: (layerId: string, statusObj: { status: LayerStatus; error?: string }) => void;
  setLayerMetadata: (layerId: string, meta: Partial<LayerMetadata>) => void;
  setLayerData: (layerId: string, features: MapFeature[], meta?: Partial<LayerMetadata>) => void;

  setSelectedFeature: (feature: MapFeature | null) => void;
  setRoute: (route: MapRoute | null) => void;
  clearRoute: () => void;
  clearSelection: () => void;

  dispatchCommand: (command: MapCommand) => void;
  consumeCommand: (revision: number) => void;

  setUserInteracting: (interacting: boolean) => void;
  recordUserInteraction: () => void;
  clearMap: () => void;
  resetMap: () => void;
}

export const useMapStore = create<MapStoreState>((set, get) => ({
  // Default Global Center View
  longitude: 15.0,
  latitude: 25.0,
  zoom: 1.8,
  pitch: 0,
  bearing: 0,

  providerMode: "hybrid",
  quality: "auto",
  isReady: false,
  terrain3D: false,

  pmtilesUrl: null,
  pmtilesTileType: "vector",
  pmtilesMetadata: null,
  availableArchives: [],
  onlineSourceUrl: "https://tiles.openfreemap.org/planet",
  customStyleUrl: null,
  customVectorSourceUrl: null,

  // Zero-intelligence default invariant
  activeLayers: {},
  layerStatus: {},
  layerMetadata: {},
  layerData: {},

  selectedFeature: null,
  route: null,

  pendingCommand: null,
  commandRevision: 0,

  userInteracting: false,
  lastUserInteractionTimestamp: 0,

  setCamera: (camera) => set((s) => ({ ...s, ...camera })),
  setReady: (ready) => set({ isReady: ready }),
  setProviderMode: (mode) => set({ providerMode: mode }),
  setQuality: (quality) => set({ quality }),
  setPmtilesUrl: (url, tileType = "vector", metadata = null) =>
    set({ pmtilesUrl: url, pmtilesTileType: tileType, pmtilesMetadata: metadata }),

  fetchAvailableArchives: async () => {
    try {
      const res = await fetch("/api/geo/pmtiles/list");
      if (res.ok) {
        const data = await res.json();
        if (Array.isArray(data.archives)) {
          set({ availableArchives: data.archives });
        }
      }
    } catch {
      // Offline fallback / mock
    }
  },

  setOnlineSourceUrl: (url) => set({ onlineSourceUrl: url }),
  setCustomStyleUrl: (url) => set({ customStyleUrl: url }),
  setCustomVectorSourceUrl: (url) => set({ customVectorSourceUrl: url }),
  setTerrain3D: (enabled) => set({ terrain3D: enabled }),

  setLayerEnabled: (layerId, enabled) =>
    set((s) => ({
      activeLayers: { ...s.activeLayers, [layerId]: enabled },
      layerStatus: {
        ...s.layerStatus,
        [layerId]: enabled
          ? s.layerStatus[layerId] || { status: "loading" }
          : { status: "idle" },
      },
      layerMetadata: {
        ...s.layerMetadata,
        [layerId]: {
          status: enabled ? "loading" : "idle",
          count: enabled ? s.layerMetadata[layerId]?.count || 0 : 0,
          attribution: s.layerMetadata[layerId]?.attribution || "",
          lastUpdated: s.layerMetadata[layerId]?.lastUpdated || 0,
        },
      },
      // If disabled, immediately clear active data
      layerData: enabled ? s.layerData : { ...s.layerData, [layerId]: [] },
    })),

  toggleLayer: (layerId) => {
    const current = !!get().activeLayers[layerId];
    get().setLayerEnabled(layerId, !current);
  },

  setLayerStatus: (layerId, statusObj) =>
    set((s) => ({
      layerStatus: { ...s.layerStatus, [layerId]: statusObj },
      layerMetadata: {
        ...s.layerMetadata,
        [layerId]: {
          status: statusObj.status,
          error: statusObj.error,
          count: s.layerMetadata[layerId]?.count || 0,
          attribution: s.layerMetadata[layerId]?.attribution || "",
          lastUpdated: s.layerMetadata[layerId]?.lastUpdated || Date.now(),
        },
      },
    })),

  setLayerMetadata: (layerId, meta) =>
    set((s) => ({
      layerMetadata: {
        ...s.layerMetadata,
        [layerId]: {
          status: meta.status || s.layerMetadata[layerId]?.status || "ready",
          count: meta.count !== undefined ? meta.count : s.layerMetadata[layerId]?.count || 0,
          attribution: meta.attribution || s.layerMetadata[layerId]?.attribution || "",
          lastUpdated: meta.lastUpdated || s.layerMetadata[layerId]?.lastUpdated || Date.now(),
          error: meta.error,
        },
      },
    })),

  setLayerData: (layerId, features, meta) =>
    set((s) => ({
      layerData: { ...s.layerData, [layerId]: features },
      layerStatus: { ...s.layerStatus, [layerId]: { status: "ready" } },
      layerMetadata: {
        ...s.layerMetadata,
        [layerId]: {
          status: "ready",
          count: features.length,
          attribution: meta?.attribution || s.layerMetadata[layerId]?.attribution || "",
          lastUpdated: meta?.lastUpdated || Date.now(),
        },
      },
    })),

  setSelectedFeature: (feature) => set({ selectedFeature: feature }),
  setRoute: (route) => set({ route }),
  clearRoute: () => set({ route: null }),
  clearSelection: () => set({ selectedFeature: null }),

  dispatchCommand: (command) =>
    set((s) => ({
      pendingCommand: { command, revision: s.commandRevision + 1 },
      commandRevision: s.commandRevision + 1,
    })),

  consumeCommand: (revision) =>
    set((s) => {
      if (s.pendingCommand && s.pendingCommand.revision === revision) {
        return { pendingCommand: null };
      }
      return {};
    }),

  setUserInteracting: (interacting) =>
    set({
      userInteracting: interacting,
      lastUserInteractionTimestamp: interacting ? Date.now() : get().lastUserInteractionTimestamp,
    }),

  recordUserInteraction: () =>
    set({
      userInteracting: true,
      lastUserInteractionTimestamp: Date.now(),
    }),

  clearMap: () =>
    set({
      selectedFeature: null,
      route: null,
      activeLayers: {},
      layerStatus: {},
      layerData: {},
    }),

  resetMap: () =>
    set({
      longitude: 15.0,
      latitude: 25.0,
      zoom: 1.8,
      pitch: 0,
      bearing: 0,
      selectedFeature: null,
      route: null,
      activeLayers: {},
      layerStatus: {},
      layerData: {},
      userInteracting: false,
    }),
}));
