import { useEffect, useRef, type ReactElement } from "react";
import type { WorkspaceInstance } from "../../layout/workspaceStore";
import { MapEngine, useMapStore } from "../../map";
import type { MapCommand, MapFeature, MapRoute } from "../../map/types";

export function MapWorkspace({ workspace }: { workspace: WorkspaceInstance }): ReactElement {
  const contentStr = JSON.stringify(workspace.contentState || {});
  const lastDispatchedPayloadRef = useRef<string>("");

  const dispatchCommand = useMapStore((s) => s.dispatchCommand);
  const setLayerEnabled = useMapStore((s) => s.setLayerEnabled);
  const setSelectedFeature = useMapStore((s) => s.setSelectedFeature);
  const setRoute = useMapStore((s) => s.setRoute);

  // Synchronize incoming presentation intent payload to MapStore
  useEffect(() => {
    if (contentStr === lastDispatchedPayloadRef.current) return;
    lastDispatchedPayloadRef.current = contentStr;

    let content: Record<string, unknown> = {};
    try {
      content = JSON.parse(contentStr);
    } catch {
      return;
    }

    // 1. Direct command payload
    if (content.command && typeof content.command === "object") {
      dispatchCommand(content.command as MapCommand);
      return;
    }

    // 2. Direct route payload
    if (content.route && typeof content.route === "object") {
      const routeData = content.route as MapRoute;
      setRoute(routeData);
      dispatchCommand({
        type: "set_route",
        route: routeData,
        fit: true,
      });
      return;
    }

    // 3. Location / FlyTo coordinates payload
    const coords = (content.center || content.coordinates || content.location) as [number, number] | undefined;
    if (Array.isArray(coords) && coords.length >= 2) {
      const zoom = typeof content.zoom === "number" ? content.zoom : 11;
      dispatchCommand({
        type: "fly_to",
        longitude: coords[0],
        latitude: coords[1],
        zoom,
      });

      if (content.name || content.title) {
        const feat: MapFeature = {
          id: `loc_${Date.now()}`,
          label: String(content.name || content.title),
          description: content.description ? String(content.description) : undefined,
          coordinates: [coords[0], coords[1]],
          category: content.category ? String(content.category) : "Location",
          severity: "normal",
          color: "#00f0ff",
        };
        setSelectedFeature(feat);
      }
      return;
    }

    // 4. Intelligence layer activation payload
    if (content.layer || content.enable_layer) {
      const layerId = String(content.layer || content.enable_layer);
      setLayerEnabled(layerId, true);
    }
  }, [contentStr, dispatchCommand, setLayerEnabled, setSelectedFeature, setRoute]);

  return (
    <div className="w-full h-full relative">
      <MapEngine />
    </div>
  );
}
