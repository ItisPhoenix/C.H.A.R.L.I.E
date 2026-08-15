import type { ReactElement } from "react";
import type { ActionSpec, PrimitiveSpec } from "../surfaceSchema";
import { TextPrimitive } from "./TextPrimitive";
import { MetricPrimitive } from "./MetricPrimitive";
import { ProgressPrimitive } from "./ProgressPrimitive";
import { ListPrimitive } from "./ListPrimitive";
import { TablePrimitive } from "./TablePrimitive";
import { ChartPrimitive } from "./ChartPrimitive";
import { TimelinePrimitive } from "./TimelinePrimitive";
import { ImagePrimitive } from "./ImagePrimitive";
import { SourceEvidencePrimitive } from "./SourceEvidencePrimitive";
import { StatusPrimitive, BadgePrimitive, DividerPrimitive } from "./StatusPrimitive";
import { ActionPrimitive } from "./ActionPrimitive";
import { SpatialMapPrimitive } from "./SpatialMapPrimitive";
import { DensityHeatmapPrimitive } from "./DensityHeatmapPrimitive";
import { TelemetryGaugesPrimitive } from "./TelemetryGaugesPrimitive";
import { ProcessTelemetryPrimitive } from "./ProcessTelemetryPrimitive";
import { LayoutContainer } from "./LayoutContainer";

export function UnknownPrimitive({ primitive }: { primitive: PrimitiveSpec }): ReactElement {
  return (
    <div className="p-2.5 rounded-lg border border-amber-500/30 bg-amber-950/20 text-amber-300 text-xs font-mono my-1">
      <span className="font-bold">[Unsupported Component: {primitive.type}]</span>
    </div>
  );
}

export function PrimitiveNode({
  primitive,
  onAction,
}: {
  primitive: PrimitiveSpec;
  onAction?: (action: ActionSpec) => void;
}): ReactElement {
  const type = String(primitive.type || "").toLowerCase();

  switch (type) {
    case "heading":
    case "text":
      return <TextPrimitive primitive={primitive} />;

    case "metric":
      return <MetricPrimitive primitive={primitive} />;

    case "progress":
      return <ProgressPrimitive primitive={primitive} />;

    case "list":
      return <ListPrimitive primitive={primitive} />;

    case "table":
      return <TablePrimitive primitive={primitive} />;

    case "chart":
      return <ChartPrimitive primitive={primitive} />;

    case "timeline":
      return <TimelinePrimitive primitive={primitive} />;

    case "image":
      return <ImagePrimitive primitive={primitive} />;

    case "source":
    case "evidence":
      return <SourceEvidencePrimitive primitive={primitive} />;

    case "status":
      return <StatusPrimitive primitive={primitive} />;

    case "badge":
      return <BadgePrimitive primitive={primitive} />;

    case "divider":
      return <DividerPrimitive />;

    case "action":
      return <ActionPrimitive primitive={primitive} onAction={onAction} />;

    case "spatial_map":
    case "map":
    case "map_placeholder":
      return <SpatialMapPrimitive primitive={primitive} />;

    case "density_heatmap":
    case "heatmap":
      return <DensityHeatmapPrimitive primitive={primitive} />;

    case "telemetry_gauges":
    case "gauges":
      return <TelemetryGaugesPrimitive primitive={primitive} />;

    case "process_telemetry":
    case "processes":
      return <ProcessTelemetryPrimitive primitive={primitive} />;

    case "layout": {
      const layoutData = (primitive.data?.layout as unknown) ?? { type: "stack", gap: 8 };
      return (
        <LayoutContainer layout={layoutData as unknown as import("../surfaceSchema").LayoutSpec}>
          {primitive.children?.map((child, idx) => (
            <PrimitiveNode key={child.id || `child_${idx}`} primitive={child} onAction={onAction} />
          ))}
        </LayoutContainer>
      );
    }

    default:
      return <UnknownPrimitive primitive={primitive} />;
  }
}
