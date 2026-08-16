import { useEffect, useRef, useState, type ReactElement } from "react";
import * as maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { MapboxOverlay } from "@deck.gl/mapbox";

import { useMapStore } from "./mapStore";
import { resolveBasemapStyle } from "./providers/basemapProvider";
import { calculateBounds, calculateFlyDuration, CHARLIE_SAFE_PADDING } from "./camera";
import { LAYER_BY_ID } from "./layers/registry";
import {
  createIntelligencePointLayer,
  createRouteLayer,
  createSelectionPulseLayer,
} from "./layers/renderers";
import type { MapCommand, MapFeature } from "./types";
import { MapContextCard } from "./overlays/MapContextCard";
import { LayerControls } from "./overlays/LayerControls";
import { MapToolbar } from "./overlays/MapToolbar";
import { MapAttribution } from "./overlays/MapAttribution";

type RenderMode = "interleaved" | "overlay" | "maplibre_only" | "svg_fallback";

export function MapEngine(): ReactElement {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const overlayRef = useRef<MapboxOverlay | null>(null);
  const abortControllersRef = useRef<Map<string, AbortController>>(new Map());

  const [renderMode, setRenderMode] = useState<RenderMode>("interleaved");
  const [initError, setInitError] = useState<string | null>(null);

  const providerMode = useMapStore((s) => s.providerMode);
  const pmtilesUrl = useMapStore((s) => s.pmtilesUrl);
  const onlineSourceUrl = useMapStore((s) => s.onlineSourceUrl);
  const activeLayers = useMapStore((s) => s.activeLayers);
  const layerData = useMapStore((s) => s.layerData);
  const selectedFeature = useMapStore((s) => s.selectedFeature);
  const route = useMapStore((s) => s.route);
  const pendingCommand = useMapStore((s) => s.pendingCommand);

  const setReady = useMapStore((s) => s.setReady);
  const setCamera = useMapStore((s) => s.setCamera);
  const setSelectedFeature = useMapStore((s) => s.setSelectedFeature);
  const setLayerData = useMapStore((s) => s.setLayerData);
  const setLayerStatus = useMapStore((s) => s.setLayerStatus);
  const consumeCommand = useMapStore((s) => s.consumeCommand);
  const recordUserInteraction = useMapStore((s) => s.recordUserInteraction);

  // 1. Initialize MapLibre GL instance with 4-Tier Fallback Hierarchy
  useEffect(() => {
    if (!containerRef.current) return;

    // Check WebGL availability
    if (typeof window !== "undefined") {
      const canvas = document.createElement("canvas");
      const gl2 = canvas.getContext("webgl2");
      const gl1 = canvas.getContext("webgl");
      if (!gl2 && !gl1) {
        setRenderMode("svg_fallback");
        setInitError("WebGL hardware acceleration unavailable");
        return;
      }
    }

    try {
      const style = resolveBasemapStyle({
        mode: providerMode,
        pmtilesUrl,
        onlineSourceUrl,
      });

      const storeState = useMapStore.getState();

      const map = new maplibregl.Map({
        container: containerRef.current,
        style: style as maplibregl.StyleSpecification,
        center: [storeState.longitude, storeState.latitude],
        zoom: storeState.zoom,
        pitch: storeState.pitch,
        bearing: storeState.bearing,
        maxPitch: 75,
        attributionControl: false,
      });

      mapRef.current = map;

      // Tier 1: Try Interleaved deck.gl overlay
      try {
        const overlay = new MapboxOverlay({
          interleaved: true,
          layers: [],
        });
        overlayRef.current = overlay;
        map.addControl(overlay as unknown as maplibregl.IControl);
        setRenderMode("interleaved");
      } catch (deckInterleaveErr) {
        console.warn("[MapEngine] Interleaved mode failed, falling back to overlay mode:", deckInterleaveErr);
        try {
          // Tier 2: Try standard non-interleaved deck.gl overlay
          const overlay = new MapboxOverlay({
            interleaved: false,
            layers: [],
          });
          overlayRef.current = overlay;
          map.addControl(overlay as unknown as maplibregl.IControl);
          setRenderMode("overlay");
        } catch (deckOverlayErr) {
          // Tier 3: MapLibre Only
          console.warn("[MapEngine] Deck.gl overlay failed, falling back to MapLibre-only:", deckOverlayErr);
          setRenderMode("maplibre_only");
        }
      }

      map.on("load", () => {
        setReady(true);
      });

      map.on("movestart", () => {
        recordUserInteraction();
      });

      map.on("moveend", () => {
        const center = map.getCenter();
        setCamera({
          longitude: center.lng,
          latitude: center.lat,
          zoom: map.getZoom(),
          pitch: map.getPitch(),
          bearing: map.getBearing(),
        });
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
      console.warn("[MapEngine] MapLibre initialization failed:", err);
      setRenderMode("svg_fallback");
      setInitError(err instanceof Error ? err.message : "MapLibre GL initialization failed");
    }
  }, [providerMode, pmtilesUrl, onlineSourceUrl, setCamera, setReady, recordUserInteraction]);

  // 2. Fetcher Lifecycle for Active Intelligence Layers
  useEffect(() => {
    for (const [layerId, isEnabled] of Object.entries(activeLayers)) {
      const def = LAYER_BY_ID.get(layerId);
      if (!def) continue;

      if (isEnabled) {
        if (layerData[layerId] && layerData[layerId].length > 0) continue;

        if (def.fetcher) {
          if (abortControllersRef.current.has(layerId)) {
            abortControllersRef.current.get(layerId)!.abort();
          }

          const controller = new AbortController();
          abortControllersRef.current.set(layerId, controller);

          setLayerStatus(layerId, { status: "loading", error: undefined });

          def
            .fetcher(controller.signal)
            .then((features) => {
              setLayerData(layerId, features);
            })
            .catch((err) => {
              if (controller.signal.aborted) return;
              console.warn(`[MapEngine] Layer '${layerId}' fetch failed:`, err);
              setLayerStatus(layerId, {
                status: "error",
                error: err instanceof Error ? err.message : "Fetch failed",
              });
            });
        } else if (def.requiresCredential) {
          setLayerStatus(layerId, {
            status: "unconfigured",
            error: "Provider credentials not configured",
          });
        }
      } else {
        if (abortControllersRef.current.has(layerId)) {
          abortControllersRef.current.get(layerId)!.abort();
          abortControllersRef.current.delete(layerId);
        }
      }
    }
  }, [activeLayers, layerData, setLayerData, setLayerStatus]);

  // 3. Update deck.gl Overlay Layers (Tiers 1 & 2)
  useEffect(() => {
    if (!overlayRef.current) return;

    const layers: unknown[] = [];

    const pulseLayer = createSelectionPulseLayer(selectedFeature);
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
          }
        );
        layers.push(pointLayer);
      }
    }

    if (route) {
      const routeLayers = createRouteLayer(route, () => {
        // Route clicked
      });
      layers.push(...routeLayers);
    }

    overlayRef.current.setProps({
      layers: layers as never,
    });
  }, [activeLayers, layerData, selectedFeature, route, setSelectedFeature]);

  // 4. Command Execution Engine
  useEffect(() => {
    if (!pendingCommand || !mapRef.current) return;

    const map = mapRef.current;
    const cmd: MapCommand = pendingCommand.command;
    const revision = pendingCommand.revision;

    try {
      switch (cmd.type) {
        case "fly_to": {
          const currentCenter = map.getCenter();
          const duration =
            cmd.durationMs ??
            calculateFlyDuration(
              currentCenter.lng,
              currentCenter.lat,
              cmd.longitude,
              cmd.latitude
            );

          map.flyTo({
            center: [cmd.longitude, cmd.latitude],
            zoom: cmd.zoom ?? map.getZoom(),
            pitch: cmd.pitch ?? map.getPitch(),
            bearing: cmd.bearing ?? map.getBearing(),
            duration,
            essential: true,
          });
          break;
        }

        case "ease_to": {
          map.easeTo({
            center: [cmd.longitude, cmd.latitude],
            zoom: cmd.zoom ?? map.getZoom(),
            pitch: cmd.pitch ?? map.getPitch(),
            bearing: cmd.bearing ?? map.getBearing(),
            duration: cmd.durationMs ?? 600,
            essential: true,
          });
          break;
        }

        case "fit_bounds": {
          map.fitBounds(cmd.bounds, {
            padding: { ...CHARLIE_SAFE_PADDING, ...(cmd.padding || {}) },
            duration: cmd.durationMs ?? 900,
          });
          break;
        }

        case "set_route": {
          if (cmd.fit && cmd.route.geometry && cmd.route.geometry.length > 0) {
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

        case "focus_location": {
          map.flyTo({
            center: [cmd.coordinates[0], cmd.coordinates[1]],
            zoom: cmd.zoom ?? 11,
            duration: 800,
            essential: true,
          });
          break;
        }

        case "zoom_in": {
          map.zoomIn({ duration: 300 });
          break;
        }

        case "zoom_out": {
          map.zoomOut({ duration: 300 });
          break;
        }

        case "reset_north": {
          map.resetNorth({ duration: 500 });
          break;
        }

        case "set_pitch": {
          map.easeTo({ pitch: cmd.pitch, duration: 450 });
          break;
        }

        case "reset_map": {
          map.flyTo({
            center: [15.0, 25.0],
            zoom: 1.8,
            pitch: 0,
            bearing: 0,
            duration: 900,
          });
          break;
        }

        case "select_feature": {
          if (cmd.flyTo && cmd.feature) {
            map.flyTo({
              center: [cmd.feature.coordinates[0], cmd.feature.coordinates[1]],
              zoom: Math.max(map.getZoom(), 8),
              duration: 700,
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
  }, [pendingCommand, consumeCommand]);

  // Tier 4: SVG Emergency Fallback UI
  if (renderMode === "svg_fallback") {
    return (
      <div className="w-full h-full relative flex items-center justify-center bg-slate-950 p-6 text-center font-mono">
        <div className="max-w-md p-6 rounded-2xl border border-cyan-500/30 bg-slate-900/80 shadow-2xl text-cyan-200 text-xs">
          <div className="text-base font-bold mb-2">🌐 Spatial Engine Fallback Active</div>
          <p className="text-slate-400 leading-relaxed mb-4">
            WebGL2 acceleration is not active in this environment. The spatial subsystem is operating in lightweight mode.
          </p>
          {initError && (
            <div className="text-[10px] text-amber-400 bg-slate-950/80 p-2 rounded border border-amber-500/20 mb-4">
              {initError}
            </div>
          )}
          <button
            type="button"
            onClick={() => setRenderMode("interleaved")}
            className="px-4 py-1.5 rounded-lg bg-cyan-950 border border-cyan-400/50 text-cyan-300 text-xs font-bold hover:bg-cyan-900 transition cursor-pointer"
          >
            Retry WebGL Engine
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="w-full h-full relative overflow-hidden select-none">
      {/* 1. MapLibre GL Canvas Container (Edge-to-Edge Spatial Canvas) */}
      <div ref={containerRef} className="w-full h-full absolute inset-0 z-0 bg-[#020710]" />

      {/* 2. Canonical Context Card Floating Overlay */}
      <MapContextCard />

      {/* 3. Layer Controls Drawer */}
      <LayerControls />

      {/* 4. Minimal HUD Toolbar */}
      <MapToolbar />

      {/* 5. Restrained Legal Attribution */}
      <MapAttribution />
    </div>
  );
}
