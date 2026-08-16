import type { ReactElement } from "react";
import {
  ContextCard,
  ContextCardHeader,
  ContextCardBody,
  ContextCardMetadata,
  ContextCardActions,
} from "../../ui/context";
import { useMapStore } from "../mapStore";

export function MapContextCard(): ReactElement | null {
  const selectedFeature = useMapStore((s) => s.selectedFeature);
  const route = useMapStore((s) => s.route);
  const setSelectedFeature = useMapStore((s) => s.setSelectedFeature);
  const setRoute = useMapStore((s) => s.setRoute);
  const dispatchCommand = useMapStore((s) => s.dispatchCommand);

  // If a route is active and no specific point is selected, display route card
  if (!selectedFeature && route) {
    const isGeodesic = route.mode === "geodesic_measurement";
    return (
      <div className="absolute top-16 left-6 z-30 max-w-sm w-full pointer-events-auto">
        <ContextCard variant="standard" elevation="floating">
          <ContextCardHeader
            title={`${route.startLabel} → ${route.destinationLabel}`}
            category={isGeodesic ? "GEODESIC DISTANCE MEASUREMENT" : "TACTICAL ROUTE NAVIGATION"}
            badge={isGeodesic ? "GEODESIC" : (route.mode ? route.mode.toUpperCase() : "DRIVING")}
            badgeVariant={isGeodesic ? "amber" : "cyan"}
            onClose={() => setRoute(null)}
          />
          <ContextCardBody>
            <p className="text-[12.5px] text-slate-200">
              {isGeodesic
                ? "Direct great-circle point-to-point measurement corridor across spherical coordinates."
                : "Optimal navigation corridor calculated across real transportation network geometry."}
            </p>
            {!isGeodesic && route.steps && route.steps.length > 0 && (
              <div className="mt-2.5 pt-2 border-t border-cyan-500/10 space-y-1">
                <div className="text-[10px] text-cyan-400 font-bold uppercase tracking-wider">
                  WAYPOINTS & MANEUVERS
                </div>
                {route.steps.slice(0, 3).map((step, idx) => (
                  <div key={idx} className="flex items-start justify-between text-[11px] text-slate-300 font-sans">
                    <span className="truncate max-w-[200px]">{step.instruction}</span>
                    <span className="text-cyan-400 font-mono text-[10px]">{step.distance}</span>
                  </div>
                ))}
              </div>
            )}
          </ContextCardBody>
          <ContextCardMetadata
            distance={`${route.distanceKm} km`}
            duration={route.durationMin ? `${route.durationMin} mins` : undefined}
          />
          <ContextCardActions
            actions={[
              {
                id: "fit-route",
                label: "Fit Corridor",
                variant: "primary",
                onClick: () => {
                  dispatchCommand({
                    type: "set_route",
                    route,
                    fit: true,
                  });
                },
              },
              {
                id: "clear-route",
                label: "Clear Route",
                variant: "subtle",
                onClick: () => setRoute(null),
              },
            ]}
          />
        </ContextCard>
      </div>
    );
  }

  if (!selectedFeature) return null;

  const severityVariant =
    selectedFeature.severity === "critical"
      ? "rose"
      : selectedFeature.severity === "high"
        ? "amber"
        : selectedFeature.severity === "medium"
          ? "amber"
          : "cyan";

  return (
    <div className="absolute top-16 left-6 z-30 max-w-sm w-full pointer-events-auto">
      <ContextCard
        variant={selectedFeature.severity === "critical" ? "warning" : "location"}
        elevation="floating"
      >
        <ContextCardHeader
          title={selectedFeature.label}
          category={selectedFeature.category || "GEOSPATIAL FEATURE"}
          badge={selectedFeature.severity ? selectedFeature.severity.toUpperCase() : undefined}
          badgeVariant={severityVariant}
          timestamp={
            selectedFeature.timestamp
              ? new Date(selectedFeature.timestamp).toLocaleTimeString()
              : undefined
          }
          onClose={() => setSelectedFeature(null)}
        />

        <ContextCardBody>
          <p className="text-[12.5px] text-slate-200">
            {selectedFeature.description || "Intelligence event anchored on spatial coordinates."}
          </p>
        </ContextCardBody>

        <ContextCardMetadata
          source={selectedFeature.source}
          coordinates={selectedFeature.coordinates}
        />

        <ContextCardActions
          actions={[
            {
              id: "fly-to",
              label: "Fly to Location",
              variant: "primary",
              onClick: () => {
                dispatchCommand({
                  type: "fly_to",
                  longitude: selectedFeature.coordinates[0],
                  latitude: selectedFeature.coordinates[1],
                  zoom: 12,
                });
              },
            },
            {
              id: "dismiss",
              label: "Dismiss",
              variant: "subtle",
              onClick: () => setSelectedFeature(null),
            },
          ]}
        />
      </ContextCard>
    </div>
  );
}
