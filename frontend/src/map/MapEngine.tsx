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
  resolveEffectiveQuality,
} from "./layers/renderers";
import { LAYER_BY_ID } from "./layers/registry";
import { calculateBounds, calculateFlyDuration, CHARLIE_SAFE_PADDING } from "./camera";
import type { RenderMode } from "./types";
import { MapToolbar } from "./overlays/MapToolbar";
import { LayerControls } from "./overlays/LayerControls";
import { MapAttribution } from "./overlays/MapAttribution";
import { MapContextCard } from "./overlays/MapContextCard";
import { SpatialMapFallback } from "../composer/primitives/SpatialMapFallback";

function detectWebGL2Support(): boolean {
  try {
    const canvas = document.createElement("canvas");
    return typeof window !== "undefined" && !!window.WebGL2RenderingContext && !!canvas.getContext("webgl2");
  } catch {
    return false;
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
  const pmtilesTileType = useMapStore((s) => s.pmtilesTileType);
  const pmtilesMetadata = useMapStore((s) => s.pmtilesMetadata);
  const onlineSourceUrl = useMapStore((s) => s.onlineSourceUrl);
  const customStyleUrl = useMapStore((s) => s.customStyleUrl);
  const customVectorSourceUrl = useMapStore((s) => s.customVectorSourceUrl);
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

  // 1. Initialize MapLibre & Deck.gl Instance with Honest Fallback Model (WebGL2 Required)
  useEffect(() => {
    if (!mapContainerRef.current) return;

    const hasWebGL2 = detectWebGL2Support();
    if (!hasWebGL2) {
      console.warn("[MapEngine] WebGL2 not supported. Falling back to Tier-D SVG Spatial Vector.");
      setRenderMode("svg_fallback");
      setReady(true);
      return;
    }

    try {
      const style = resolveBasemapStyle({
        mode: providerMode,
        pmtilesUrl,
        pmtilesTileType,
        pmtilesMetadata,
        customStyleUrl,
        customVectorSourceUrl,
      });

      const effectiveQuality = resolveEffectiveQuality(quality);

      const map = new maplibregl.Map({
        container: mapContainerRef.current,
        style,
        center: [15.0, 25.0],
        zoom: 1.8,
        pitch: effectiveQuality === "low" ? 0 : 0,
        bearing: 0,
        attributionControl: false,
        maxPitch: effectiveQuality === "low" ? 0 : 60,
      });
      mapRef.current = map;

      // Tier A: Deck.gl Interleaved Overlay
      try {
        const overlay = new MapboxOverlay({
          interleaved: true,
          layers: [],
        });
        overlayRef.current = overlay;
        map.addControl(overlay as unknown as maplibregl.IControl);
        setRenderMode("interleaved");
      } catch (deckInterleaveErr) {
        console.warn("[MapEngine] Tier A interleaved mode failed, trying Tier B non-interleaved overlay:", deckInterleaveErr);
        try {
          const overlay = new MapboxOverlay({
            interleaved: false,
            layers: [],
          });
          overlayRef.current = overlay;
          map.addControl(overlay as unknown as maplibregl.IControl);
          setRenderMode("overlay");
        } catch (overlayErr) {
          console.warn("[MapEngine] Tier B overlay failed, falling back to Tier C MapLibre-only:", overlayErr);
          setRenderMode("maplibre_only");
        }
      }

      map.on("load", () => {
        setReady(true);
      });

      // Camera Interruption Listeners (Only genuine user interaction stops camera)
      const handleUserInteractionStart = (e?: { originalEvent?: unknown }) => {
        if (e && !e.originalEvent) {
          // Programmatic camera change (flyTo/easeTo/setPitch), do not interrupt
          return;
        }
        if (mapRef.current) {
          mapRef.current.stop(); // Stop active programmatic camera animation
        }
        setUserInteracting(true);
      };

      const handleUserInteractionEnd = () => {
        setUserInteracting(false);
      };

      map.on("dragstart", handleUserInteractionStart);
      map.on("touchstart", handleUserInteractionStart);
      map.on("rotatestart", handleUserInteractionStart);
      map.on("boxzoomstart", handleUserInteractionStart);
      map.on("wheel", handleUserInteractionStart);

      map.on("dragend", handleUserInteractionEnd);
      map.on("touchend", handleUserInteractionEnd);
      map.on("rotateend", handleUserInteractionEnd);
      map.on("pitchend", handleUserInteractionEnd);

      // Direct container DOM listeners for unambiguous user interaction detection
      const container = mapContainerRef.current;
      if (container) {
        container.addEventListener("pointerdown", () => handleUserInteractionStart());
        container.addEventListener("wheel", () => handleUserInteractionStart(), { passive: true });
        container.addEventListener("touchstart", () => handleUserInteractionStart(), { passive: true });
      }

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
  }, [
    providerMode,
    pmtilesUrl,
    pmtilesTileType,
    pmtilesMetadata,
    onlineSourceUrl,
    customStyleUrl,
    customVectorSourceUrl,
    quality,
    setCamera,
    setReady,
    setUserInteracting,
  ]);

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
              status: (res.status as "ready" | "loading" | "error" | "unconfigured") || "ready",
              attribution: res.attribution || def.attribution,
              lastUpdated: res.timestamp || Date.now(),
              count: res.features.length,
            });
          })
          .catch((err) => {
            if (err?.name === "AbortError") return;
            setLayerStatus(layerId, {
              status: "error",
              error: err instanceof Error ? err.message : "Layer data fetch failed",
            });
          });
      } else {
        // Disabled: cancel any in-flight request immediately
        if (abortControllersRef.current[layerId]) {
          abortControllersRef.current[layerId].abort();
          delete abortControllersRef.current[layerId];
        }
      }
    }
  }, [activeLayers, layerData, layerMetadata, setLayerData, setLayerStatus]);

  // 3. Update Deck.gl Layers on State Change
  useEffect(() => {
    if (!overlayRef.current || renderMode === "maplibre_only" || renderMode === "svg_fallback") return;

    const deckLayers: any[] = [];

    // Intelligence Layers
    for (const [layerId, isEnabled] of Object.entries(activeLayers)) {
      if (isEnabled && layerData[layerId] && layerData[layerId].length > 0) {
        const pointLayer = createIntelligencePointLayer(
          layerId,
          layerData[layerId],
          (feature) => setSelectedFeature(feature),
          quality
        );
        deckLayers.push(pointLayer);
      }
    }

    // Selected Feature Pulse Layer
    if (selectedFeature) {
      const pulseLayer = createSelectionPulseLayer(selectedFeature, quality);
      if (pulseLayer) deckLayers.push(pulseLayer);
    }

    // Active Route Corridor Layer
    if (route && route.geometry && route.geometry.length > 1) {
      const routeLayers = createRouteLayer(route, () => {}, quality);
      deckLayers.push(...routeLayers);
    }

    overlayRef.current.setProps({
      layers: deckLayers,
    });
  }, [activeLayers, layerData, selectedFeature, route, quality, renderMode, setSelectedFeature]);

  // 4. MapCommand Execution Queue Listener (Supports all 16 commands)
  useEffect(() => {
    if (!pendingCommand) return;
    const { command, revision } = pendingCommand;
    const map = mapRef.current;

    switch (command.type) {
      case "fly_to": {
        if (map) {
          const currentCenter = map.getCenter();
          const duration =
            command.durationMs ??
            calculateFlyDuration(currentCenter.lng, currentCenter.lat, command.longitude, command.latitude);

          map.flyTo({
            center: [command.longitude, command.latitude],
            zoom: command.zoom ?? map.getZoom(),
            pitch: command.pitch ?? map.getPitch(),
            bearing: command.bearing ?? map.getBearing(),
            duration,
            essential: true,
            padding: CHARLIE_SAFE_PADDING,
          });
        } else {
          setCamera({
            longitude: command.longitude,
            latitude: command.latitude,
            zoom: command.zoom,
            pitch: command.pitch,
            bearing: command.bearing,
          });
        }
        break;
      }

      case "ease_to": {
        if (map) {
          map.easeTo({
            center: [command.longitude, command.latitude],
            zoom: command.zoom ?? map.getZoom(),
            pitch: command.pitch ?? map.getPitch(),
            bearing: command.bearing ?? map.getBearing(),
            duration: command.durationMs ?? 800,
            padding: CHARLIE_SAFE_PADDING,
          });
        } else {
          setCamera({
            longitude: command.longitude,
            latitude: command.latitude,
            zoom: command.zoom,
            pitch: command.pitch,
            bearing: command.bearing,
          });
        }
        break;
      }

      case "fit_bounds": {
        if (map) {
          map.fitBounds(command.bounds, {
            padding: {
              ...CHARLIE_SAFE_PADDING,
              ...command.padding,
            },
            duration: command.durationMs ?? 1200,
            maxZoom: 16,
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
        if (map) map.resetNorthPitch({ duration: 600 });
        break;
      }

      case "set_pitch": {
        if (map) map.setPitch(command.pitch);
        break;
      }

      case "set_bearing": {
        if (map) map.setBearing(command.bearing);
        break;
      }

      case "focus_location": {
        if (map) {
          map.flyTo({
            center: command.coordinates,
            zoom: command.zoom ?? 12,
            duration: 1200,
            padding: CHARLIE_SAFE_PADDING,
          });
        }
        break;
      }

      case "select_feature": {
        setSelectedFeature(command.feature);
        if (command.feature && command.flyTo !== false) {
          if (map) {
            map.flyTo({
              center: [command.feature.coordinates[0], command.feature.coordinates[1]],
              zoom: Math.max(map.getZoom(), 8),
              duration: 1000,
              padding: CHARLIE_SAFE_PADDING,
            });
          }
        }
        break;
      }

      case "set_layer": {
        setLayerEnabled(command.layerId, command.enabled);
        break;
      }

      case "toggle_layer": {
        toggleLayer(command.layerId);
        break;
      }

      case "set_route": {
        setRoute(command.route);
        if (command.fit !== false && command.route.geometry.length > 1) {
          const bounds = calculateBounds(command.route.geometry);
          if (bounds && map) {
            map.fitBounds(bounds, {
              padding: CHARLIE_SAFE_PADDING,
              duration: 1200,
            });
          }
        }
        break;
      }

      case "clear_route": {
        clearRoute();
        break;
      }

      case "clear_selection": {
        clearSelection();
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
            duration: 1000,
          });
        }
        break;
      }
    }

    consumeCommand(revision);
  }, [
    pendingCommand,
    consumeCommand,
    setCamera,
    setLayerEnabled,
    setSelectedFeature,
    setRoute,
    clearRoute,
    clearSelection,
    resetMapStore,
    toggleLayer,
  ]);

  // Fallback Render View (Tier-D: Lightweight SVG Equirectangular Projection)
  if (renderMode === "svg_fallback") {
    const vectorNodes: any[] = [];

    // Origin and Destination if route exists
    if (route) {
      vectorNodes.push({
        id: "origin",
        label: route.startLabel,
        x: ((route.start[0] + 180) / 360) * 100,
        y: ((90 - route.start[1]) / 180) * 100,
        color: "#00f0ff",
      });
      vectorNodes.push({
        id: "dest",
        label: route.destinationLabel,
        x: ((route.destination[0] + 180) / 360) * 100,
        y: ((90 - route.destination[1]) / 180) * 100,
        color: "#38bdf8",
      });
    }

    // Active Intelligence Layers
    Object.entries(activeLayers).forEach(([layerId, enabled]) => {
      if (enabled && layerData[layerId]) {
        layerData[layerId].forEach((feat) => {
          vectorNodes.push({
            id: feat.id,
            label: feat.label,
            x: ((feat.coordinates[0] + 180) / 360) * 100,
            y: ((90 - feat.coordinates[1]) / 180) * 100,
            color: feat.color,
            severity: feat.severity,
          });
        });
      }
    });

    return (
      <div className="w-full h-full relative overflow-hidden bg-[#020710] font-mono">
        <SpatialMapFallback
          data={{
            mode: "geo",
            title: "SPATIAL VECTOR RADAR (TIER-D)",
            subtitle: initError ? `Fallback active: ${initError}` : "2D equirectangular vector projection",
            nodes: vectorNodes,
            edges: route
              ? [
                  {
                    from: "origin",
                    to: "dest",
                    type: "route",
                    active: true,
                    label: `${route.startLabel} → ${route.destinationLabel}`,
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

  return (
    <div className="w-full h-full relative overflow-hidden bg-[#020710] select-none font-mono">
      {/* 1. MapLibre GL Canvas Container */}
      <div ref={mapContainerRef} className="w-full h-full absolute inset-0 z-0" tabIndex={-1} />

      {/* 2. Top-Right Technical Compass & Controls */}
      <MapToolbar />

      {/* 3. Top-Left Intelligence Layer Toggle Panel */}
      <LayerControls />

      {/* 4. Bottom-Right Dynamic Context & Telemetry Card */}
      <MapContextCard />

      {/* 5. Bottom-Left Legal Attribution */}
      <MapAttribution renderMode={renderMode} />
    </div>
  );
}
