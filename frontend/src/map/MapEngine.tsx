import { useEffect, useRef, useState, useCallback, type ReactElement } from "react";
import * as maplibregl from "maplibre-gl";
import { MapboxOverlay } from "@deck.gl/mapbox";
import "maplibre-gl/dist/maplibre-gl.css";

import { useMapStore } from "./mapStore";
import { resolveBasemapStyle } from "./providers/basemapProvider";
import { fetchBackendIntelligenceLayer } from "./providers/intelligenceProvider";
import {
  createIntelligencePointLayer,
  createRouteLayer,
  createRouteEndpointsLayer,
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
  const syncLayersAndRouteRef = useRef<() => void>(() => {});

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
      (window as any).__CHARLIE_MAP_INSTANCE__ = map;

      // Render Mode Progression (Tier A -> Tier B -> Tier C)
      let tierInitialized = false;

      // Tier A: MapLibre + Deck.gl interleaved
      try {
        const overlayA = new MapboxOverlay({
          interleaved: true,
        });
        map.addControl(overlayA as unknown as maplibregl.IControl);
        overlayRef.current = overlayA;
        setRenderMode("interleaved");
        tierInitialized = true;
      } catch (tierAErr) {
        console.warn("[MapEngine] Tier A (interleaved) failed, falling back to Tier B:", tierAErr);
      }

      // Tier B: MapLibre + Deck.gl non-interleaved overlay (if Tier A threw)
      if (!tierInitialized) {
        try {
          const overlayB = new MapboxOverlay({
            interleaved: false,
          });
          map.addControl(overlayB as unknown as maplibregl.IControl);
          overlayRef.current = overlayB;
          setRenderMode("overlay");
          tierInitialized = true;
        } catch (tierBErr) {
          console.warn("[MapEngine] Tier B (overlay) failed, falling back to Tier C:", tierBErr);
        }
      }

      // Tier C: Native MapLibre vector layers (if Deck.gl failed)
      if (!tierInitialized) {
        overlayRef.current = null;
        setRenderMode("maplibre_only");
        tierInitialized = true;
      }

      const handleLoad = () => {
        setReady(true);
        map.resize();
        syncLayersAndRouteRef.current();
      };

      const handleStyleData = () => {
        syncLayersAndRouteRef.current();
      };

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

      const handleMoveEnd = () => {
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
      };

      const handleMapError = (e: any) => {
        if (overlayRef.current && (e?.error?.message?.includes("height") || e?.error?.message?.includes("getViewport"))) {
          console.warn("[MapEngine] Interleaved render exception detected, falling back to Tier B (overlay mode)");
          try {
            map.removeControl(overlayRef.current as unknown as maplibregl.IControl);
            const overlayB = new MapboxOverlay({ interleaved: false });
            map.addControl(overlayB as unknown as maplibregl.IControl);
            overlayRef.current = overlayB;
            setRenderMode("overlay");
            syncLayersAndRouteRef.current();
          } catch {
            overlayRef.current = null;
            setRenderMode("maplibre_only");
            syncLayersAndRouteRef.current();
          }
        }
      };

      map.on("error", handleMapError);
      map.on("load", handleLoad);
      map.on("styledata", handleStyleData);
      map.on("dragstart", handleUserInteractionStart);
      map.on("touchstart", handleUserInteractionStart);
      map.on("rotatestart", handleUserInteractionStart);
      map.on("boxzoomstart", handleUserInteractionStart);
      map.on("wheel", handleUserInteractionStart);
      map.on("dragend", handleUserInteractionEnd);
      map.on("touchend", handleUserInteractionEnd);
      map.on("rotateend", handleUserInteractionEnd);
      map.on("pitchend", handleUserInteractionEnd);
      map.on("moveend", handleMoveEnd);

      // Direct container DOM listeners for unambiguous user interaction detection
      const onPointerDown = () => handleUserInteractionStart();
      const onWheel = () => handleUserInteractionStart();
      const onTouchStart = () => handleUserInteractionStart();

      const container = mapContainerRef.current;
      if (container) {
        container.addEventListener("pointerdown", onPointerDown);
        container.addEventListener("wheel", onWheel, { passive: true });
        container.addEventListener("touchstart", onTouchStart, { passive: true });
      }

      return () => {
        setReady(false);
        try {
          if (container) {
            container.removeEventListener("pointerdown", onPointerDown);
            container.removeEventListener("wheel", onWheel);
            container.removeEventListener("touchstart", onTouchStart);
          }
          map.off("error", handleMapError);
          map.off("load", handleLoad);
          map.off("styledata", handleStyleData);
          map.off("dragstart", handleUserInteractionStart);
          map.off("touchstart", handleUserInteractionStart);
          map.off("rotatestart", handleUserInteractionStart);
          map.off("boxzoomstart", handleUserInteractionStart);
          map.off("wheel", handleUserInteractionStart);
          map.off("dragend", handleUserInteractionEnd);
          map.off("touchend", handleUserInteractionEnd);
          map.off("rotateend", handleUserInteractionEnd);
          map.off("pitchend", handleUserInteractionEnd);
          map.off("moveend", handleMoveEnd);

          if (overlayRef.current) {
            map.removeControl(overlayRef.current as unknown as maplibregl.IControl);
            overlayRef.current = null;
          }
          map.remove();
          mapRef.current = null;
          (window as any).__CHARLIE_MAP_INSTANCE__ = null;
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

  const syncLayersAndRoute = useCallback(() => {
    const map = mapRef.current;
    if (!map) return;
    if (!map.getStyle()) {
      map.once("styledata", () => syncLayersAndRoute());
      return;
    }

    const hasValidRoute = Boolean(route && (route.geometry?.length > 1 || (route as any).coordinates?.length > 1));
    const routeCoords = (hasValidRoute ? (route!.geometry || (route as any).coordinates) : []) as [number, number][];

    try {
      // Mutually Exclusive Tiers:
      // TIER A / TIER B: Deck.gl exclusively owns route, endpoint, and intelligence layers
      if ((renderMode === "interleaved" || renderMode === "overlay") && overlayRef.current) {
        const deckLayers: any[] = [];

        // 1. Route corridor and endpoints via Deck.gl
        if (hasValidRoute) {
          const routeLayers = createRouteLayer(route!, () => {}, quality);
          deckLayers.push(...routeLayers);
          const endpointLayer = createRouteEndpointsLayer(route!, quality);
          if (endpointLayer) deckLayers.push(endpointLayer);
        }

        // 2. Intelligence points via Deck.gl
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

        // 3. Selection pulse via Deck.gl
        if (selectedFeature) {
          const pulseLayer = createSelectionPulseLayer(selectedFeature, quality);
          if (pulseLayer) deckLayers.push(pulseLayer);
        }

        overlayRef.current.setProps({ layers: deckLayers });

        // Guarantee native MapLibre GeoJSON sources do NOT render duplicate geometry
        const routeSource = map.getSource("charlie-route-source") as maplibregl.GeoJSONSource | undefined;
        if (routeSource) {
          routeSource.setData({ type: "FeatureCollection", features: [] });
        }
        const endpointSource = map.getSource("charlie-route-endpoints") as maplibregl.GeoJSONSource | undefined;
        if (endpointSource) {
          endpointSource.setData({ type: "FeatureCollection", features: [] });
        }
        for (const layerId of Object.keys(activeLayers)) {
          const intelSource = map.getSource(`charlie-intel-${layerId}`) as maplibregl.GeoJSONSource | undefined;
          if (intelSource) {
            intelSource.setData({ type: "FeatureCollection", features: [] });
          }
        }
      } else if (renderMode === "maplibre_only") {
        // TIER C: Native MapLibre GeoJSON exclusively owns visualization; Deck.gl overlay is cleared
        if (overlayRef.current) {
          overlayRef.current.setProps({ layers: [] });
        }

        // 1. Route Line Source & Layers
        let routeSource = map.getSource("charlie-route-source") as maplibregl.GeoJSONSource | undefined;
        const lineData: any = {
          type: "FeatureCollection",
          features: hasValidRoute ? [
            {
              type: "Feature",
              properties: {},
              geometry: {
                type: "LineString",
                coordinates: routeCoords,
              },
            },
          ] : [],
        };

        if (!routeSource) {
          map.addSource("charlie-route-source", {
            type: "geojson",
            data: lineData,
          });
          map.addLayer({
            id: "charlie-route-glow",
            type: "line",
            source: "charlie-route-source",
            paint: {
              "line-color": "rgba(0, 240, 255, 0.4)",
              "line-width": 8,
              "line-blur": 3,
            },
          });
          map.addLayer({
            id: "charlie-route-line",
            type: "line",
            source: "charlie-route-source",
            paint: {
              "line-color": "#00f0ff",
              "line-width": 3.5,
            },
            layout: {
              "line-cap": "round",
              "line-join": "round",
            },
          });
        } else {
          routeSource.setData(lineData);
        }

        // 2. Route Endpoints (Origin & Destination Markers)
        const endpointFeatures: any[] = [];
        if (hasValidRoute && routeCoords.length >= 2) {
          endpointFeatures.push({
            type: "Feature",
            properties: {
              type: "origin",
              label: route!.startLabel || "Origin",
            },
            geometry: {
              type: "Point",
              coordinates: routeCoords[0],
            },
          });
          endpointFeatures.push({
            type: "Feature",
            properties: {
              type: "destination",
              label: route!.destinationLabel || "Destination",
            },
            geometry: {
              type: "Point",
              coordinates: routeCoords[routeCoords.length - 1],
            },
          });
        }

        let endpointSource = map.getSource("charlie-route-endpoints") as maplibregl.GeoJSONSource | undefined;
        if (!endpointSource) {
          map.addSource("charlie-route-endpoints", {
            type: "geojson",
            data: {
              type: "FeatureCollection",
              features: endpointFeatures,
            },
          });
          map.addLayer({
            id: "charlie-route-endpoints-halo",
            type: "circle",
            source: "charlie-route-endpoints",
            paint: {
              "circle-radius": 16,
              "circle-color": [
                "match",
                ["get", "type"],
                "origin",
                "rgba(34, 211, 238, 0.4)",
                "rgba(244, 63, 94, 0.4)",
              ],
              "circle-stroke-width": 2,
              "circle-stroke-color": [
                "match",
                ["get", "type"],
                "origin",
                "#22d3ee",
                "#f43f5e",
              ],
            },
          });
          map.addLayer({
            id: "charlie-route-endpoints-core",
            type: "circle",
            source: "charlie-route-endpoints",
            paint: {
              "circle-radius": 6,
              "circle-color": "#ffffff",
            },
          });
        } else {
          endpointSource.setData({
            type: "FeatureCollection",
            features: endpointFeatures,
          });
        }

        // 3. Native Intelligence Feature Layers (e.g. Earthquakes)
        for (const [layerId, isEnabled] of Object.entries(activeLayers)) {
          const features = isEnabled && layerData[layerId] ? layerData[layerId] : [];
          const geoFeatures: GeoJSON.Feature<GeoJSON.Point>[] = features.map((f) => ({
            type: "Feature" as const,
            properties: {
              id: f.id,
              label: f.label,
              category: f.category,
              severity: f.severity || "normal",
            },
            geometry: {
              type: "Point" as const,
              coordinates: f.coordinates,
            },
          }));

          let intelSource = map.getSource(`charlie-intel-${layerId}`) as maplibregl.GeoJSONSource | undefined;
          if (!intelSource) {
            map.addSource(`charlie-intel-${layerId}`, {
              type: "geojson",
              data: {
                type: "FeatureCollection",
                features: geoFeatures,
              },
            });
            map.addLayer({
              id: `charlie-intel-halo-${layerId}`,
              type: "circle",
              source: `charlie-intel-${layerId}`,
              paint: {
                "circle-radius": [
                  "match",
                  ["get", "severity"],
                  "critical", 22,
                  "high", 16,
                  "medium", 12,
                  10,
                ],
                "circle-color": [
                  "match",
                  ["get", "severity"],
                  "critical", "rgba(239, 68, 68, 0.45)",
                  "high", "rgba(249, 115, 22, 0.45)",
                  "medium", "rgba(234, 179, 8, 0.45)",
                  "rgba(34, 211, 238, 0.45)",
                ],
                "circle-stroke-width": 2,
                "circle-stroke-color": [
                  "match",
                  ["get", "severity"],
                  "critical", "#ef4444",
                  "high", "#f97316",
                  "medium", "#eab308",
                  "#22d3ee",
                ],
              },
            });
            map.addLayer({
              id: `charlie-intel-core-${layerId}`,
              type: "circle",
              source: `charlie-intel-${layerId}`,
              paint: {
                "circle-radius": [
                  "match",
                  ["get", "severity"],
                  "critical", 8,
                  "high", 7,
                  "medium", 6,
                  5,
                ],
                "circle-color": "#ffffff",
              },
            });
          } else {
            intelSource.setData({
              type: "FeatureCollection",
              features: geoFeatures,
            });
          }
        }
      }
    } catch (e) {
      console.warn("[MapEngine] Layer sync error:", e);
    }
  }, [activeLayers, layerData, selectedFeature, route, quality, renderMode, setSelectedFeature]);

  syncLayersAndRouteRef.current = syncLayersAndRoute;

  useEffect(() => {
    syncLayersAndRoute();
  }, [syncLayersAndRoute]);

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
        const coords = (command.route.geometry || (command.route as any).coordinates || []) as [number, number][];
        if (command.fit !== false && coords.length > 1) {
          const bounds = calculateBounds(coords);
          if (bounds && map) {
            map.fitBounds(bounds, {
              padding: { top: 100, bottom: 140, left: 120, right: 400 },
              maxZoom: 13,
              duration: 800,
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
      {/* 1. MapLibre GL Canvas Container (Hosts WebGL & Deck.gl layers) */}
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
