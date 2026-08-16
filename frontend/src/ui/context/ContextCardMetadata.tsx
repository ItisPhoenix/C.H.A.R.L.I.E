import type { ReactElement, ReactNode } from "react";

export interface MetadataItem {
  label: string;
  value: string | number;
  unit?: string;
  highlight?: boolean;
  color?: string;
}

export interface ContextCardMetadataProps {
  items?: MetadataItem[];
  source?: string;
  confidence?: number;
  coordinates?: [number, number]; // [lon, lat]
  distance?: string;
  duration?: string;
  children?: ReactNode;
  className?: string;
}

export function ContextCardMetadata({
  items = [],
  source,
  confidence,
  coordinates,
  distance,
  duration,
  children,
  className = "",
}: ContextCardMetadataProps): ReactElement | null {
  const hasContent =
    items.length > 0 ||
    Boolean(source) ||
    confidence !== undefined ||
    Boolean(coordinates) ||
    Boolean(distance) ||
    Boolean(duration) ||
    Boolean(children);

  if (!hasContent) return null;

  const formatCoord = (val: number, isLat: boolean) => {
    const dir = isLat ? (val >= 0 ? "N" : "S") : val >= 0 ? "E" : "W";
    return `${Math.abs(val).toFixed(3)}° ${dir}`;
  };

  return (
    <div className={`charlie-card-metadata ${className}`}>
      {source && (
        <div className="flex items-center gap-1">
          <span className="text-slate-500 uppercase">SRC:</span>
          <span className="text-cyan-300 font-semibold truncate max-w-[120px]">{source}</span>
        </div>
      )}

      {coordinates && (
        <div className="flex items-center gap-1 font-mono text-[9.5px]">
          <span className="text-slate-500">POS:</span>
          <span className="text-cyan-300">
            {formatCoord(coordinates[1], true)}, {formatCoord(coordinates[0], false)}
          </span>
        </div>
      )}

      {confidence !== undefined && (
        <div className="flex items-center gap-1">
          <span className="text-slate-500 uppercase">CONF:</span>
          <span
            className={`font-semibold ${
              confidence >= 0.8
                ? "text-emerald-400"
                : confidence >= 0.5
                  ? "text-amber-400"
                  : "text-rose-400"
            }`}
          >
            {(confidence * 100).toFixed(0)}%
          </span>
        </div>
      )}

      {distance && (
        <div className="flex items-center gap-1">
          <span className="text-slate-500 uppercase">DIST:</span>
          <span className="text-cyan-300 font-semibold">{distance}</span>
        </div>
      )}

      {duration && (
        <div className="flex items-center gap-1">
          <span className="text-slate-500 uppercase">TIME:</span>
          <span className="text-cyan-300 font-semibold">{duration}</span>
        </div>
      )}

      {items.map((item, idx) => (
        <div key={idx} className="flex items-center gap-1">
          <span className="text-slate-500 uppercase">{item.label}:</span>
          <span
            className={`font-semibold ${
              item.color ? "" : item.highlight ? "text-cyan-300" : "text-slate-200"
            }`}
            style={item.color ? { color: item.color } : undefined}
          >
            {item.value}
            {item.unit && <span className="text-slate-400 text-[9px] ml-0.5">{item.unit}</span>}
          </span>
        </div>
      ))}

      {children}
    </div>
  );
}
