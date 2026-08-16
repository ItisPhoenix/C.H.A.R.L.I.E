export interface SpatialMapNode {
  id: string;
  label: string;
  sublabel?: string;
  x: number; // 0 to 100 percentage or coordinate
  y: number; // 0 to 100 percentage or coordinate
  type?: string;
  status?: "active" | "warning" | "idle" | "error" | string;
  value?: string | number;
  color?: string;
  severity?: string;
}

export interface SpatialMapEdge {
  from: string;
  to: string;
  type?: "route" | "link" | "vector" | "dotted" | string;
  active?: boolean;
  label?: string;
}

export interface SpatialMapLayer {
  id: string;
  label: string;
  color?: string;
  defaultActive?: boolean;
}

export interface SpatialMapRadarObject {
  id?: string;
  label?: string;
  type?: string;
  status?: string;
  angle?: number;
  distance?: number;
}

export interface SpatialMapRadar {
  mode?: string;
  center?: [number, number];
  objects?: SpatialMapRadarObject[];
}

export interface SpatialMapData {
  mode?: "radar" | "geo" | "topology" | "generic_spatial";
  title?: string;
  subtitle?: string;
  nodes?: SpatialMapNode[];
  edges?: SpatialMapEdge[];
  layers?: SpatialMapLayer[];
  centerLabel?: string;
  useRealEngine?: boolean;
  radar?: SpatialMapRadar;
  objects?: SpatialMapRadarObject[];
}

