import type { ReactElement } from "react";
import type { WorkspaceInstance } from "../../layout/workspaceStore";
import { useCharlieStore } from "../../store/charlie";

export interface VisionBoundingBox {
  id: string;
  label: string;
  confidence: number;
  box: [number, number, number, number]; // [ymin, xmin, ymax, xmax] in % (0..100)
  color?: string;
}

function parseBoundingBoxes(raw: unknown, coordinateSpace: unknown, frameWidth: unknown, frameHeight: unknown): VisionBoundingBox[] {
  if (
    coordinateSpace !== "percent"
    || typeof frameWidth !== "number" || !Number.isFinite(frameWidth) || frameWidth <= 0
    || typeof frameHeight !== "number" || !Number.isFinite(frameHeight) || frameHeight <= 0
  ) return [];
  if (!Array.isArray(raw)) return [];

  return raw.flatMap((entry) => {
    if (!entry || typeof entry !== "object") return [];
    const value = entry as Record<string, unknown>;
    const rawBox = value.box;
    if (!Array.isArray(rawBox) || rawBox.length !== 4) return [];

    const id = typeof value.id === "string" && value.id.trim() ? value.id.trim() : null;
    const label = typeof value.label === "string" && value.label.trim() ? value.label.trim() : null;
    const confidence = typeof value.confidence === "number"
      && Number.isFinite(value.confidence)
      && value.confidence >= 0
      && value.confidence <= 1
      ? value.confidence
      : null;
    if (!id || !label || confidence === null) return [];

    const box = rawBox.map(Number);
    if (box.some((coordinate) => !Number.isFinite(coordinate) || coordinate < 0 || coordinate > 100)) return [];
    const [ymin, xmin, ymax, xmax] = box;
    if (ymax < ymin || xmax < xmin) return [];

    return [{
      id,
      label,
      confidence,
      box: [ymin, xmin, ymax, xmax],
      ...(typeof value.color === "string" ? { color: value.color } : {}),
    }];
  });
}

function safeMediaUrl(value: unknown): string {
  if (typeof value !== "string" || !value.trim()) return "";
  const candidate = value.trim();
  if (/^(?:data:|file:|[a-zA-Z]:[\\/]|\\\\)/i.test(candidate)) return "";
  if (candidate.startsWith("/") || candidate.startsWith("blob:") || /^https?:\/\//i.test(candidate)) return candidate;
  return "";
}

export function VisionWorkspace({ workspace }: { workspace: WorkspaceInstance }): ReactElement {
  const content = workspace.contentState || {};
  const visionObservation = useCharlieStore((state) => state.visionObservation);
  const desktopFrame = useCharlieStore((state) => state.desktopFrame);
  const title = String(content.title || workspace.title || "VISION GROUNDING WORKSPACE").replace(/^WORKSPACE\s*\/\/\s*/i, "");
  const imageUrl = safeMediaUrl(content.image_url || content.snapshot_url) || desktopFrame?.imageUrl || "";
  const boxes = parseBoundingBoxes(
    content.bounding_boxes,
    content.bounding_box_coordinate_space,
    content.frame_width,
    content.frame_height,
  );
  const status = String(content.status || content.state || "").toLowerCase();
  const isPending = content.loading === true || ["loading", "pending", "processing", "starting"].includes(status);
  const isUnavailable = content.available === false || ["unavailable", "offline", "error", "failed"].includes(status);
  const emptyMessage = isPending
    ? "WAITING FOR AUTHORITATIVE VISION OBSERVATION"
    : isUnavailable
      ? "VISION OBSERVATION UNAVAILABLE"
      : imageUrl
        ? "NO GROUNDED OBJECTS REPORTED"
        : "NO LIVE MEDIA AVAILABLE";

  return (
    <div className="w-full h-full flex flex-col justify-between font-mono select-none text-left p-2 overflow-y-auto space-y-4">
      {/* Header */}
      <div className="flex items-start justify-between border-b border-cyan-500/20 pb-3">
        <div>
          <div className="text-[10px] text-cyan-400 font-bold tracking-widest uppercase mb-0.5 flex items-center gap-2">
            <span className="w-1.5 h-1.5 rounded-full bg-cyan-400 animate-pulse" />
            LOCAL VISION PERCEPTION
          </div>
          <h1 className="text-xl font-bold text-slate-100 uppercase tracking-tight font-sans">
            {title}
          </h1>
          <div className="text-xs text-cyan-400/70 tracking-widest uppercase">
            UIA / OCR GROUNDED INFERENCE
          </div>
          <div data-testid="vision-observation-metadata" className="text-[10px] text-slate-400 tracking-wider uppercase mt-2">
            {visionObservation
              ? `OBSERVED UIA ${visionObservation.uiaCount} · OCR ${visionObservation.ocrCount}${visionObservation.observedAt ? ` · ${visionObservation.observedAt}` : ""}`
              : "NO LIVE VISION METADATA AVAILABLE"}
            {desktopFrame?.capturedAt && ` · FRAME ${desktopFrame.capturedAt}`}
          </div>
        </div>
      </div>

      {/* Main Viewport */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">
        {/* Visual Frame with Bounding Box Overlays */}
        <div className="lg:col-span-8 relative aspect-video rounded-xl border border-cyan-500/30 bg-slate-950 overflow-hidden shadow-2xl">
          {imageUrl ? (
            <img src={imageUrl} alt="Perception Frame" className="w-full h-full object-contain" />
          ) : (
            <div className="w-full h-full flex items-center justify-center bg-slate-950/80 p-6 text-center" role="status">
              <span className="text-xs font-mono text-cyan-400/80">[{emptyMessage}]</span>
            </div>
          )}

          {/* Bounding Boxes */}
          {imageUrl && boxes.map((b) => {
            const [ymin, xmin, ymax, xmax] = b.box;
            const top = `${ymin}%`;
            const left = `${xmin}%`;
            const width = `${xmax - xmin}%`;
            const height = `${ymax - ymin}%`;

            return (
              <div
                key={b.id}
                className="absolute border-2 transition-all hover:bg-cyan-500/10 pointer-events-auto cursor-pointer"
                style={{
                  top,
                  left,
                  width,
                  height,
                  borderColor: b.color || "#22d3ee",
                }}
              >
                <div
                  className="absolute -top-5 left-0 px-1.5 py-0.5 text-[9px] font-mono font-bold uppercase rounded-t"
                  style={{
                    backgroundColor: b.color || "#22d3ee",
                    color: "#020617",
                  }}
                >
                  {b.label} [{(b.confidence * 100).toFixed(0)}%]
                </div>
              </div>
            );
          })}
        </div>

        {/* Detections List & Grounding Logs */}
        <div className="lg:col-span-4 flex flex-col gap-4">
          <div className="text-left">
            <div className="text-xs font-semibold text-cyan-200 tracking-wider uppercase">
              DETECTION RESULTS
            </div>
            <div className="text-[10px] text-cyan-400/60 uppercase">
              {boxes.length > 0 ? `${boxes.length} OBJECTS GROUNDED` : "NO GROUNDED OBJECTS"}
            </div>
          </div>

          <div className="space-y-2">
            {boxes.length > 0 ? boxes.map((b) => (
              <div
                key={b.id}
                className="p-3 rounded-xl border border-cyan-500/20 bg-slate-950/60 backdrop-blur-md flex items-center justify-between gap-3 text-left hover:border-cyan-400/40 transition"
              >
                <div>
                  <div className="text-xs font-bold text-slate-200 font-mono">
                    {b.label}
                  </div>
                  <div className="text-[10px] text-slate-400 mt-0.5">
                    Bounds: [{b.box.join(", ")}]
                  </div>
                </div>
                <span className="text-[11px] font-bold text-cyan-300 font-mono">
                  {(b.confidence * 100).toFixed(0)}%
                </span>
              </div>
            )) : desktopFrame?.marks.length ? desktopFrame.marks.map((mark) => (
              <div key={mark.markId} className="p-3 border border-cyan-500/15 text-[11px] text-slate-400" data-testid="vision-mark">
                MARK {mark.markId}: {mark.name}
              </div>
            )) : <div className="p-3 border border-cyan-500/15 text-[11px] text-slate-500 italic" role="status">{emptyMessage}</div>}
          </div>
        </div>
      </div>
    </div>
  );
}
