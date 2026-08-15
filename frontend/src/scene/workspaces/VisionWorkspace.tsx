import type { ReactElement } from "react";
import type { WorkspaceInstance } from "../../layout/workspaceStore";

export interface VisionBoundingBox {
  id: string;
  label: string;
  confidence: number;
  box: [number, number, number, number]; // [ymin, xmin, ymax, xmax] in % (0..100)
  color?: string;
}

export function VisionWorkspace({ workspace }: { workspace: WorkspaceInstance }): ReactElement {
  const content = workspace.contentState || {};
  const title = String(content.title || workspace.title || "VISION GROUNDING WORKSPACE").replace(/^WORKSPACE\s*\/\/\s*/i, "");
  const imageUrl = String(content.image_url || content.snapshot_url || "");
  const boxes: VisionBoundingBox[] = (content.bounding_boxes as VisionBoundingBox[]) || [
    { id: "b1", label: "DESKTOP WINDOW [Editor]", confidence: 0.96, box: [15, 10, 80, 55], color: "#22d3ee" },
    { id: "b2", label: "BUTTON [Submit]", confidence: 0.92, box: [75, 60, 85, 75], color: "#38bdf8" },
    { id: "b3", label: "TERMINAL PROMPT", confidence: 0.89, box: [20, 60, 68, 92], color: "#34d399" },
  ];

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
        </div>
      </div>

      {/* Main Viewport */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">
        {/* Visual Frame with Bounding Box Overlays */}
        <div className="lg:col-span-8 relative aspect-video rounded-xl border border-cyan-500/30 bg-slate-950 overflow-hidden shadow-2xl">
          {imageUrl ? (
            <img src={imageUrl} alt="Perception Frame" className="w-full h-full object-contain" />
          ) : (
            <div className="w-full h-full flex items-center justify-center bg-slate-950/80 relative">
              {/* Technical crosshair viewfinder grid */}
              <div className="absolute inset-0 bg-radial from-cyan-950/20 to-transparent" />
              <svg className="w-full h-full opacity-30" viewBox="0 0 400 225">
                <circle cx="200" cy="112.5" r="40" fill="none" stroke="#22d3ee" strokeWidth="1" strokeDasharray="3 3" />
                <line x1="180" y1="112.5" x2="220" y2="112.5" stroke="#22d3ee" strokeWidth="1" />
                <line x1="200" y1="92.5" x2="200" y2="132.5" stroke="#22d3ee" strokeWidth="1" />
              </svg>
              <span className="text-xs font-mono text-cyan-400/80 z-10">[LOCAL VISION SENSOR STREAM ACTIVE]</span>
            </div>
          )}

          {/* Bounding Boxes */}
          {boxes.map((b) => {
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
              {boxes.length} OBJECTS GROUNDED
            </div>
          </div>

          <div className="space-y-2">
            {boxes.map((b) => (
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
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
