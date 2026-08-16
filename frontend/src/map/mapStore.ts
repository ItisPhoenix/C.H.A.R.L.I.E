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

  // Active Tile URLs
  pmtilesUrl: string | null;
  onlineSourceUrl: string;

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
  setPmtilesUrl: (url: string | null) => void;
  setOnlineSourceUrl: (url: string) => void;
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

export const useMapStore = create<MapStoreState>((set) => ({
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
  onlineSourceUrl: "https://tiles.openfreemap.org/planet",

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
  setPmtilesUrl: (url) => set({ pmtilesUrl: url }),
  setOnlineSourceUrl: (url) => set({ onlineSourceUrl: url }),
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
          ...(s.layerMetadata[layerId] || { status: "idle" }),
          status: enabled ? "loading" : "idle",
        },
      },
    })),

  toggleLayer: (layerId) =>
    set((s) => {
      const next = !s.activeLayers[layerId];
      return {
        activeLayers: { ...s.activeLayers, [layerId]: next },
        layerStatus: {
          ...s.layerStatus,
          [layerId]: next
            ? s.layerStatus[layerId] || { status: "loading" }
            : { status: "idle" },
        },
        layerMetadata: {
          ...s.layerMetadata,
          [layerId]: {
            ...(s.layerMetadata[layerId] || { status: "idle" }),
            status: next ? "loading" : "idle",
          },
        },
      };
    }),

  setLayerStatus: (layerId, statusObj) =>
    set((s) => ({
      layerStatus: {
        ...s.layerStatus,
        [layerId]: statusObj,
      },
      layerMetadata: {
        ...s.layerMetadata,
        [layerId]: {
          ...(s.layerMetadata[layerId] || { status: "idle" }),
          status: statusObj.status,
          error: statusObj.error,
        },
      },
    })),

  setLayerMetadata: (layerId, meta) =>
    set((s) => ({
      layerMetadata: {
        ...s.layerMetadata,
        [layerId]: {
          ...(s.layerMetadata[layerId] || { status: "idle" }),
          ...meta,
        },
      },
    })),

  setLayerData: (layerId, features, meta) =>
    set((s) => ({
      layerData: { ...s.layerData, [layerId]: features },
      layerStatus: {
        ...s.layerStatus,
        [layerId]: { status: "ready" },
      },
      layerMetadata: {
        ...s.layerMetadata,
        [layerId]: {
          ...(s.layerMetadata[layerId] || { status: "idle" }),
          status: "ready",
          count: features.length,
          lastUpdated: Date.now(),
          ...meta,
        },
      },
    })),

  setSelectedFeature: (feature) => set({ selectedFeature: feature }),
  setRoute: (route) => set({ route }),
  clearRoute: () => set({ route: null }),
  clearSelection: () => set({ selectedFeature: null }),

  dispatchCommand: (command) =>
    set((s) => {
      const nextRev = s.commandRevision + 1;
      return {
        pendingCommand: { command, revision: nextRev },
        commandRevision: nextRev,
      };
    }),

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
      lastUserInteractionTimestamp: Date.now(),
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
    }),
}));
