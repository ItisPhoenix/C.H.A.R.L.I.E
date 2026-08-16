import { useEffect, useRef, useState, type ReactElement } from "react";
import * as maplibregl from "maplibre-gl";
import { MapboxOverlay } from "@deck.gl/mapbox";
import "maplibre-gl/dist/maplibre-gl.css";

import { useMapStore } from "./mapStore";
import { resolveBasemapStyle } from "./providers/basemapProvider";
import { fetchBackendIntelligenceLayer } from "./providers/intelligenceProvider";
import {
  createIntelligencePointLayer,
  createRouteLayer,
  createSelectionPulseLayer,
} from "./layers/renderers";
import { LAYER_BY_ID } from "./layers/registry";
import { calculateBounds, calculateFlyDuration, CHARLIE_SAFE_PADDING } from "./camera";
import type { MapCommand, MapFeature, RenderMode } from "./types";
import { MapToolbar } from "./overlays/MapToolbar";
import { LayerControls } from "./overlays/LayerControls";
import { MapAttribution } from "./overlays/MapAttribution";
import { MapContextCard } from "./overlays/MapContextCard";
import { SpatialMapPrimitive } from "../composer/primitives/SpatialMapPrimitive";

function detectWebGLSupport(): { webgl2: boolean; webgl1: boolean } {
  try {
    const canvas = document.createElement("canvas");
    const gl2 = typeof window !== "undefined" && !!window.WebGL2RenderingContext && !!canvas.getContext("webgl2");
    const gl1 =
      typeof window !== "undefined" &&
      !!window.WebGLRenderingContext &&
      !!(canvas.getContext("webgl") || canvas.getContext("experimental-webgl"));
    return { webgl2: gl2, webgl1: gl1 };
  } catch {
    return { webgl2: false, webgl1: false };
  }
}

export function MapEngine(): ReactElement {
  const mapContainerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const overlayRef = useRef<MapboxOverlay | null>(null);
  const abortControllersRef = useRef<Record<string, AbortController>>({});

  const [renderMode, setRenderMode] = useState<RenderMode>("interleaved");
  const [initError, setInitError] = useState<string | null>(null);

  // Store State
  const providerMode = useMapStore((s) => s.providerMode);
  const quality = useMapStore((s) => s.quality);
  const pmtilesUrl = useMapStore((s) => s.pmtilesUrl);
  const onlineSourceUrl = useMapStore((s) => s.onlineSourceUrl);
  const activeLayers = useMapStore((s) => s.activeLayers);
  const layerData = useMapStore((s) => s.layerData);
  const layerMetadata = useMapStore((s) => s.layerMetadata);
  const selectedFeature = useMapStore((s) => s.selectedFeature);
  const route = useMapStore((s) => s.route);
  const pendingCommand = useMapStore((s) => s.pendingCommand);

  // Store Actions
  const setCamera = useMapStore((s) => s.setCamera);
  const setReady = useMapStore((s) => s.setReady);
  const setLayerData = useMapStore((s) => s.setLayerData);
  const setLayerStatus = useMapStore((s) => s.setLayerStatus);
  const setLayerEnabled = useMapStore((s) => s.setLayerEnabled);
  const toggleLayer = useMapStore((s) => s.toggleLayer);
  const setSelectedFeature = useMapStore((s) => s.setSelectedFeature);
  const setRoute = useMapStore((s) => s.setRoute);
  const clearRoute = useMapStore((s) => s.clearRoute);
  const clearSelection = useMapStore((s) => s.clearSelection);
  const resetMapStore = useMapStore((s) => s.resetMap);
  const consumeCommand = useMapStore((s) => s.consumeCommand);
  const setUserInteracting = useMapStore((s) => s.setUserInteracting);

  // 1. Initialize MapLibre & Deck.gl Instance with 4-Tier Fallback Hierarchy
  useEffect(() => {
    if (!mapContainerRef.current) return;

    const { webgl2, webgl1 } = detectWebGLSupport();
    if (!webgl2 && !webgl1) {
      console.warn("[MapEngine] No WebGL support detected. Falling back to Tier-4 SVG Spatial Vector.");
      setRenderMode("svg_fallback");
      setReady(true);
      return;
    }

    try {
      const style = resolveBasemapStyle({
        mode: providerMode,
        pmtilesUrl,
        onlineSourceUrl,
      });

      const map = new maplibregl.Map({
        container: mapContainerRef.current,
        style,
        center: [15.0, 25.0],
        zoom: 1.8,
        pitch: quality === "low" ? 0 : 0,
        bearing: 0,
        attributionControl: false,
        maxPitch: quality === "low" ? 0 : 60,
      });
      mapRef.current = map;

      // Tier 1 & 2: Deck.gl Overlay Setup
      if (webgl2) {
        try {
          const overlay = new MapboxOverlay({
            interleaved: true,
            layers: [],
          });
          overlayRef.current = overlay;
          map.addControl(overlay as unknown as maplibregl.IControl);
          setRenderMode("interleaved");
        } catch (deckInterleaveErr) {
          console.warn("[MapEngine] Interleaved mode failed, trying non-interleaved overlay:", deckInterleaveErr);
          try {
            const overlay = new MapboxOverlay({
              interleaved: false,
              layers: [],
            });
            overlayRef.current = overlay;
            map.addControl(overlay as unknown as maplibregl.IControl);
            setRenderMode("overlay");
          } catch (overlayErr) {
            console.warn("[MapEngine] Deck.gl overlay failed, falling back to MapLibre-only:", overlayErr);
            setRenderMode("maplibre_only");
          }
        }
      } else {
        // WebGL1: Non-interleaved overlay or maplibre-only
        try {
          const overlay = new MapboxOverlay({
            interleaved: false,
            layers: [],
          });
          overlayRef.current = overlay;
          map.addControl(overlay as unknown as maplibregl.IControl);
          setRenderMode("overlay");
        } catch {
          setRenderMode("maplibre_only");
        }
      }

      map.on("load", () => {
        setReady(true);
      });

      // Real Camera Interruption Listeners
      const handleUserInteractionStart = () => {
        if (mapRef.current) {
          mapRef.current.stop(); // Immediately stop programmatic camera flyTo/easeTo
        }
        setUserInteracting(true);
      };

      const handleUserInteractionEnd = () => {
        setUserInteracting(false);
      };

      map.on("dragstart", handleUserInteractionStart);
      map.on("touchstart", handleUserInteractionStart);
      map.on("rotatestart", handleUserInteractionStart);
      map.on("pitchstart", handleUserInteractionStart);
      map.on("boxzoomstart", handleUserInteractionStart);
      map.on("wheel", handleUserInteractionStart);

      map.on("dragend", handleUserInteractionEnd);
      map.on("touchend", handleUserInteractionEnd);
      map.on("rotateend", handleUserInteractionEnd);
      map.on("pitchend", handleUserInteractionEnd);

      map.on("moveend", () => {
        const center = map.getCenter();
        setCamera({
          longitude: center.lng,
          latitude: center.lat,
          zoom: map.getZoom(),
          pitch: map.getPitch(),
          bearing: map.getBearing(),
        });
        if (!map.isMoving()) {
          setUserInteracting(false);
        }
      });

      return () => {
        setReady(false);
        try {
          if (overlayRef.current) {
            map.removeControl(overlayRef.current as unknown as maplibregl.IControl);
            overlayRef.current = null;
          }
          map.remove();
          mapRef.current = null;
        } catch {
          // Cleanup ignore
        }
      };
    } catch (err) {
      console.warn("[MapEngine] MapLibre initialization failed, falling back to SVG renderer:", err);
      setRenderMode("svg_fallback");
      setInitError(err instanceof Error ? err.message : "MapLibre GL initialization failed");
      setReady(true);
    }
  }, [providerMode, pmtilesUrl, onlineSourceUrl, quality, setCamera, setReady, setUserInteracting]);

  // 2. Strict Intelligence Layer Lifecycle (TTL, Abort, Zero Polling on Disabled)
  useEffect(() => {
    for (const [layerId, isEnabled] of Object.entries(activeLayers)) {
      const def = LAYER_BY_ID.get(layerId);
      if (!def) continue;

      if (isEnabled) {
        const meta = layerMetadata[layerId];
        const ttlMs = (def.ttlSec ?? 60) * 1000;
        const isFresh = meta?.lastUpdated && Date.now() - meta.lastUpdated < ttlMs;

        // If fresh and has data, skip re-fetching
        if (isFresh && layerData[layerId] && layerData[layerId].length > 0) {
          continue;
        }

        // Cancel previous pending request if any
        if (abortControllersRef.current[layerId]) {
          abortControllersRef.current[layerId].abort();
        }

        const controller = new AbortController();
        abortControllersRef.current[layerId] = controller;

        setLayerStatus(layerId, { status: "loading" });

        fetchBackendIntelligenceLayer(layerId, controller.signal)
          .then((res) => {
            setLayerData(layerId, res.features, {
              status: "ready",
              attribution: res.attribution || def.attribution,
              lastUpdated: Date.now(),
              count: res.features.length,
            });
          })
          .catch((err) => {
            if (controller.signal.aborted) return;
            setLayerStatus(layerId, {
              status: "error",
              error: err instanceof Error ? err.message : "Layer fetch failed",
            });
          });
      } else {
        // Disabled: Abort in-flight request and ensure no background polling occurs
        if (abortControllersRef.current[layerId]) {
          abortControllersRef.current[layerId].abort();
          delete abortControllersRef.current[layerId];
        }
      }
    }

    return () => {
      // Abort all in-flight requests on unmount
      Object.values(abortControllersRef.current).forEach((ctrl) => ctrl.abort());
      abortControllersRef.current = {};
    };
  }, [activeLayers, layerMetadata, layerData, setLayerData, setLayerStatus]);

  // 3. Update deck.gl Overlay Layers (Tiers 1 & 2)
  useEffect(() => {
    if (!overlayRef.current) return;

    const layers: unknown[] = [];

    const pulseLayer = createSelectionPulseLayer(selectedFeature, quality);
    if (pulseLayer) {
      layers.push(pulseLayer);
    }

    for (const [layerId, isEnabled] of Object.entries(activeLayers)) {
      if (!isEnabled) continue;
      const features = layerData[layerId];
      if (features && features.length > 0) {
        const pointLayer = createIntelligencePointLayer(
          layerId,
          features,
          (f: MapFeature) => {
            setSelectedFeature(f);
          },
          quality
        );
        layers.push(pointLayer);
      }
    }

    if (route) {
      const routeLayers = createRouteLayer(route, () => {}, quality);
      layers.push(...routeLayers);
    }

    overlayRef.current.setProps({
      layers: layers as never,
    });
  }, [activeLayers, layerData, selectedFeature, route, quality, setSelectedFeature]);

  // 4. Complete Command Execution Engine (All 16 Declared Commands)
  useEffect(() => {
    if (!pendingCommand) return;

    const cmd: MapCommand = pendingCommand.command;
    const revision = pendingCommand.revision;
    const map = mapRef.current;

    try {
      switch (cmd.type) {
        case "fly_to": {
          if (map) {
            const currentCenter = map.getCenter();
            const duration =
              cmd.durationMs ??
              calculateFlyDuration(currentCenter.lng, currentCenter.lat, cmd.longitude, cmd.latitude);

            map.flyTo({
              center: [cmd.longitude, cmd.latitude],
              zoom: cmd.zoom ?? map.getZoom(),
              pitch: cmd.pitch ?? map.getPitch(),
              bearing: cmd.bearing ?? map.getBearing(),
              duration,
              essential: true,
            });
          }
          break;
        }

        case "ease_to": {
          if (map) {
            map.easeTo({
              center: [cmd.longitude, cmd.latitude],
              zoom: cmd.zoom ?? map.getZoom(),
              pitch: cmd.pitch ?? map.getPitch(),
              bearing: cmd.bearing ?? map.getBearing(),
              duration: cmd.durationMs ?? 600,
              essential: true,
            });
          }
          break;
        }

        case "fit_bounds": {
          if (map) {
            map.fitBounds(cmd.bounds, {
              padding: { ...CHARLIE_SAFE_PADDING, ...(cmd.padding || {}) },
              duration: cmd.durationMs ?? 900,
            });
          }
          break;
        }

        case "focus_location": {
          if (map) {
            map.flyTo({
              center: [cmd.coordinates[0], cmd.coordinates[1]],
              zoom: cmd.zoom ?? 11,
              duration: 800,
              essential: true,
            });
          }
          break;
        }

        case "zoom_in": {
          if (map) map.zoomIn({ duration: 300 });
          break;
        }

        case "zoom_out": {
          if (map) map.zoomOut({ duration: 300 });
          break;
        }

        case "reset_north": {
          if (map) map.resetNorth({ duration: 500 });
          break;
        }

        case "set_pitch": {
          if (map) map.easeTo({ pitch: cmd.pitch, duration: 450 });
          break;
        }

        case "set_bearing": {
          if (map) map.easeTo({ bearing: cmd.bearing, duration: 450 });
          break;
        }

        case "select_feature": {
          setSelectedFeature(cmd.feature);
          if (cmd.flyTo && cmd.feature && map) {
            map.flyTo({
              center: [cmd.feature.coordinates[0], cmd.feature.coordinates[1]],
              zoom: Math.max(map.getZoom(), 8),
              duration: 700,
            });
          }
          break;
        }

        case "clear_selection": {
          clearSelection();
          break;
        }

        case "set_layer": {
          setLayerEnabled(cmd.layerId, cmd.enabled);
          break;
        }

        case "toggle_layer": {
          toggleLayer(cmd.layerId);
          break;
        }

        case "set_route": {
          setRoute(cmd.route);
          if (cmd.fit && cmd.route.geometry && cmd.route.geometry.length > 0 && map) {
            const bounds = calculateBounds(cmd.route.geometry);
            if (bounds) {
              map.fitBounds(bounds, {
                padding: CHARLIE_SAFE_PADDING,
                duration: 1000,
              });
            }
          }
          break;
        }

        case "clear_route": {
          clearRoute();
          break;
        }

        case "reset_map": {
          resetMapStore();
          if (map) {
            map.flyTo({
              center: [15.0, 25.0],
              zoom: 1.8,
              pitch: 0,
              bearing: 0,
              duration: 900,
            });
          }
          break;
        }
      }
    } catch (err) {
      console.warn("[MapEngine] Command execution error:", err);
    } finally {
      consumeCommand(revision);
    }
  }, [
    pendingCommand,
    consumeCommand,
    setSelectedFeature,
    clearSelection,
    setLayerEnabled,
    toggleLayer,
    setRoute,
    clearRoute,
    resetMapStore,
  ]);

  // Tier 4: Interactive SVG Spatial Renderer Fallback
  if (renderMode === "svg_fallback") {
    // Transform intelligence layer points to spatial map nodes for vector visualizer
    const vectorNodes: { id: string; x: number; y: number; label: string; severity?: string }[] = [];
    Object.entries(activeLayers).forEach(([layerId, isEnabled]) => {
      if (isEnabled && layerData[layerId]) {
        layerData[layerId].forEach((feat) => {
          vectorNodes.push({
            id: feat.id,
            x: ((feat.coordinates[0] + 180) / 360) * 1000,
            y: ((90 - feat.coordinates[1]) / 180) * 500,
            label: feat.label,
            severity: feat.severity,
          });
        });
      }
    });

    return (
      <div className="w-full h-full relative overflow-hidden bg-[#020710] font-mono">
        <SpatialMapPrimitive
          data={{
            mode: "geo",
            useRealEngine: false,
            title: "SPATIAL VECTOR RADAR (TIER-4)",
            subtitle: initError ? `Fallback active: ${initError}` : "Fallback 2D vector projection",
            nodes: vectorNodes,
            edges: route
              ? [
                  {
                    from: "origin",
                    to: "dest",
                    type: "route",
                    active: true,
                  },
                ]
              : [],
          }}
        />
        <MapToolbar />
        <LayerControls />
        <MapContextCard />
        <MapAttribution renderMode="svg_fallback" />
      </div>
    );
  }

  // Tiers 1, 2, 3: Full Spatial Canvas
  return (
    <div className="w-full h-full relative overflow-hidden bg-[#020710] select-none">
      <div ref={mapContainerRef} className="w-full h-full" />
      <MapToolbar />
      <LayerControls />
      <MapContextCard />
      <MapAttribution renderMode={renderMode} />
    </div>
  );
}
