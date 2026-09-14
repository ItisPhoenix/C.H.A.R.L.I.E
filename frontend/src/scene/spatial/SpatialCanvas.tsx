import { useCallback, useEffect, useMemo, useState, type ReactElement } from "react";
import type { SpatialMapData, SpatialMapNode } from "../../composer/primitives/SpatialMapTypes";
import { SpatialMapPrimitive } from "../../composer/primitives/SpatialMapPrimitive";
import { useCharlieStore } from "../../store/charlie";
import { INITIAL_VISUAL_RUNTIME, type VisualRuntimePhase } from "../../runtime/visualRuntime";
import { EnvironmentLayer } from "../EnvironmentLayer";
import { DominantSurface } from "./DominantSurface";
import { SpatialOverlay } from "./SpatialOverlay";
import { ContextLayerStack, type ContextLayerEntry } from "./ContextLayerStack";
import { CoreDock } from "./CoreDock";
import { SceneTransitionController } from "./SceneTransitionController";
import { VisionProofSurface, type VisionProofBox } from "./VisionProofSurface";
import "../scene.css";
import "./spatial.css";

export type SpatialProofScene = "idle" | "research" | "vision";
export type SpatialProofContext = "base" | "selected";

export interface SpatialProofFinding {
  id?: string;
  title: string;
  detail: string;
  confidence?: number;
  source_ids?: string[];
}

interface SpatialCanvasProps {
  initialScene: SpatialProofScene;
  initialContext?: SpatialProofContext;
  researchMap: SpatialMapData;
  researchFindings: readonly SpatialProofFinding[];
  visionBoxes: readonly VisionProofBox[];
}

declare global {
  interface Window {
    __CHARLIE_SPATIAL_PROOF__?: {
      setScene: (scene: SpatialProofScene) => void;
      selectContext: () => void;
      openAnalysis: () => void;
      closeContext: () => void;
    };
  }
}

function baseContext(scene: SpatialProofScene): ContextLayerEntry {
  return { id: `${scene}-base`, label: "BASE CONTEXT" };
}

function activeFinding(findings: readonly SpatialProofFinding[]): SpatialProofFinding {
  return findings[0] || {
    title: "No finding selected",
    detail: "No grounded finding was supplied by the TEST/MOCK fixture.",
  };
}

function ResearchContextField({ nodes }: { nodes: readonly SpatialMapNode[] }): ReactElement {
  return (
    <div className="spatial-research-context" data-testid="research-context-field" aria-hidden="true">
      <svg viewBox="0 0 1000 500" preserveAspectRatio="none">
        <defs>
          <linearGradient id="research-field-base" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" stopColor="#0a202a" stopOpacity=".7" />
            <stop offset=".48" stopColor="#06121b" stopOpacity=".42" />
            <stop offset="1" stopColor="#02070d" stopOpacity=".8" />
          </linearGradient>
          <radialGradient id="research-field-focus" cx="50%" cy="50%" r="52%">
            <stop offset="0" stopColor="#1a5665" stopOpacity=".34" />
            <stop offset=".62" stopColor="#0b2e3d" stopOpacity=".1" />
            <stop offset="1" stopColor="#02070d" stopOpacity="0" />
          </radialGradient>
          <pattern id="research-field-grid" width="50" height="50" patternUnits="userSpaceOnUse">
            <path d="M 50 0 L 0 0 0 50" fill="none" stroke="#55cbe7" strokeOpacity=".12" strokeWidth="1" />
          </pattern>
          <filter id="research-context-blur" x="-20%" y="-20%" width="140%" height="140%">
            <feGaussianBlur stdDeviation="6" />
          </filter>
        </defs>

        <rect width="1000" height="500" fill="url(#research-field-base)" />
        <rect width="1000" height="500" fill="url(#research-field-focus)" />
        <rect width="1000" height="500" fill="url(#research-field-grid)" opacity=".58" />

        <g fill="none" stroke="#54c9e7" strokeOpacity=".15" strokeWidth="1">
          <path d="M18 382 C132 332 224 350 314 302 S488 218 594 246 S802 328 982 238" />
          <path d="M-40 432 C118 394 236 424 362 370 S610 278 760 338 S900 392 1040 354" strokeDasharray="9 15" />
          <path d="M80 148 C218 114 320 154 408 126 S634 72 920 144" strokeDasharray="2 13" />
          <path d="M158 54 L220 454 M322 22 L382 476 M716 28 L660 464 M862 44 L808 446" strokeOpacity=".09" />
        </g>

        <g fill="#0d303c" fillOpacity=".32" stroke="#5bd4ee" strokeOpacity=".18" strokeWidth="1">
          <path d="M42 284 L172 244 L256 278 L222 356 L84 366 Z" />
          <path d="M272 182 L412 146 L506 188 L462 268 L300 278 Z" />
          <path d="M566 290 L702 232 L844 270 L812 356 L632 372 Z" />
          <path d="M736 96 L902 80 L962 144 L894 202 L760 184 Z" />
        </g>

        <g fill="none" stroke="#93eaff" strokeOpacity=".18" strokeWidth="1.4">
          <circle cx="500" cy="250" r="178" />
          <circle cx="500" cy="250" r="118" strokeDasharray="4 14" />
          <path d="M500 48 V452 M298 250 H702" strokeDasharray="2 11" strokeOpacity=".12" />
        </g>

        <g fill="none" stroke="#22d3ee" strokeOpacity=".2" strokeWidth="2">
          <path d="M118 365 C226 330 308 318 406 274 S610 204 730 154" />
          <path d="M312 418 C380 350 436 316 500 250 S634 176 846 138" strokeDasharray="14 11" />
        </g>

        <g>
          {nodes
            .filter((node) => node.type === "source" || node.type === "finding")
            .map((node) => (
              <g key={`context-${node.id}`}>
                <circle cx={node.x * 10} cy={node.y * 5} r="15" fill={node.color || "#22d3ee"} fillOpacity=".06" filter="url(#research-context-blur)" />
                <circle cx={node.x * 10} cy={node.y * 5} r="4" fill="none" stroke={node.color || "#22d3ee"} strokeOpacity=".46" />
              </g>
            ))}
        </g>

        <g fill="#9fd0de" fillOpacity=".66" fontFamily="monospace" fontSize="9" letterSpacing="2">
          <text x="42" y="458">SOURCE-BOUND CORRIDORS</text>
          <text x="770" y="458">EVIDENCE PATH / ACTIVE</text>
        </g>
      </svg>
    </div>
  );
}

export function SpatialCanvas({
  initialScene,
  initialContext = "base",
  researchMap,
  researchFindings,
  visionBoxes,
}: SpatialCanvasProps): ReactElement {
  const [scene, setScene] = useState<SpatialProofScene>(initialScene);
  const [contextStack, setContextStack] = useState<ContextLayerEntry[]>([baseContext(initialScene)]);
  const coreState = useCharlieStore((state) => state.coreState);
  const visualRuntime = useCharlieStore((state) => state.visualRuntime);
  const finding = useMemo(() => activeFinding(researchFindings), [researchFindings]);

  const syncProofRuntime = useCallback((nextScene: SpatialProofScene) => {
    const phase: VisualRuntimePhase = nextScene === "idle" ? "idle" : "acting";
    useCharlieStore.setState({
      coreState: nextScene === "idle" ? "idle" : "working",
      visualRuntime: {
        ...INITIAL_VISUAL_RUNTIME,
        phase,
        label: nextScene === "idle" ? "IDLE" : nextScene === "research" ? "RESEARCHING" : "VISION ACTIVE",
        detail: "TEST/MOCK spatial proof",
        correlation: { sessionId: "visual-lab-session", turnId: null, taskId: null, requestId: null },
        updatedAt: new Date().toISOString(),
      },
    });
  }, []);

  const setProofScene = useCallback((nextScene: SpatialProofScene) => {
    setScene(nextScene);
    setContextStack([baseContext(nextScene)]);
    syncProofRuntime(nextScene);
  }, [syncProofRuntime]);

  const selectContext = useCallback(() => {
    if (scene === "idle") return;
    setContextStack((current) => current.length > 1
      ? current
      : [...current, { id: `${scene}-selected`, label: "SELECTED CONTEXT" }]);
  }, [scene]);

  const openAnalysis = useCallback(() => {
    if (scene !== "research") return;
    setContextStack((current) => current.some((entry) => entry.id === "research-analysis")
      ? current
      : [...current, { id: "research-analysis", label: "ANALYSIS" }]);
  }, [scene]);

  const closeContext = useCallback(() => {
    setContextStack((current) => current.length > 1 ? current.slice(0, -1) : current);
  }, []);

  useEffect(() => {
    setProofScene(initialScene);
    if (initialContext === "selected" && initialScene !== "idle") {
      setContextStack([baseContext(initialScene), { id: `${initialScene}-selected`, label: "SELECTED CONTEXT" }]);
    }
  }, [initialContext, initialScene, setProofScene]);

  useEffect(() => {
    window.__CHARLIE_SPATIAL_PROOF__ = { setScene: setProofScene, selectContext, openAnalysis, closeContext };
    return () => {
      delete window.__CHARLIE_SPATIAL_PROOF__;
    };
  }, [closeContext, openAnalysis, selectContext, setProofScene]);

  const activeContext = contextStack.at(-1);
  const hasContextOverlay = Boolean(activeContext && !activeContext.id.endsWith("-base"));
  const corePosition = scene === "idle" ? "center" : "dock_bottom_right";

  return (
    <main
      className="charlie-scene-root spatial-canvas-root"
      data-visual-lab="TEST/MOCK"
      data-spatial-scene={scene}
      data-scene-mode={scene === "idle" ? "idle" : "active"}
      data-core-position={corePosition}
      data-context-depth={contextStack.length}
      aria-label="TEST/MOCK adaptive spatial canvas"
    >
      <EnvironmentLayer corePosition={corePosition} hasWorkspace={scene !== "idle"} />

      <SceneTransitionController transitionKey={`${scene}:${activeContext?.id || "none"}`}>
        {scene === "research" && (
          <DominantSurface id="research-map" kind="research" ariaLabel="Research evidence map">
            <div className="spatial-research-surface">
              <ResearchContextField nodes={researchMap.nodes || []} />
              <div className="spatial-research-map-layer">
                <SpatialMapPrimitive data={{ ...researchMap, useRealEngine: false }} />
              </div>
              <button
                type="button"
                className="spatial-evidence-hotspot"
                data-testid="research-select-finding"
                onClick={selectContext}
                aria-label={`Select ${finding.title}`}
              >
                <span className="spatial-evidence-hotspot__label">OBSERVED RISK</span>
              </button>
            </div>
          </DominantSurface>
        )}

        {scene === "vision" && (
          <DominantSurface id="vision-media" kind="vision" ariaLabel="Vision media frame">
            <VisionProofSurface boxes={visionBoxes} onSelectBox={selectContext} />
          </DominantSurface>
        )}

        <ContextLayerStack stack={contextStack}>
          {(entry) => hasContextOverlay ? (
            scene === "research" ? (
              <SpatialOverlay
                id={entry.id}
                eyebrow={entry.id === "research-analysis" ? "ANALYSIS / CONTEXT" : "EVIDENCE / SELECTED"}
                title={entry.id === "research-analysis" ? "WHY THIS SIGNAL MATTERS" : finding.title}
                position="right"
                onClose={closeContext}
              >
                <p>{entry.id === "research-analysis"
                  ? "The selected finding remains attached to its evidence field while deeper interpretation is surfaced above the base context."
                  : finding.detail}</p>
                <div className="spatial-overlay__facts">
                  <span>CONFIDENCE <strong>{finding.confidence === undefined ? "NOT REPORTED" : `${Math.round(finding.confidence * 100)}%`}</strong></span>
                  <span>SOURCES <strong>{finding.source_ids?.length || 0} VERIFIED</strong></span>
                </div>
                {entry.id !== "research-analysis" && (
                  <button type="button" className="spatial-proof-action" data-testid="research-open-analysis" onClick={openAnalysis}>
                    OPEN ANALYSIS
                  </button>
                )}
              </SpatialOverlay>
            ) : (
              <SpatialOverlay
                id={entry.id}
                eyebrow="VISION / GROUNDING"
                title="SELECTED REGION"
                position="right"
                onClose={closeContext}
              >
                <p>{visionBoxes[0]?.label || "Grounded region"} remains attached to the underlying frame.</p>
                <div className="spatial-overlay__facts">
                  <span>CONFIDENCE <strong>{visionBoxes[0] ? `${Math.round(visionBoxes[0].confidence * 100)}%` : "NOT REPORTED"}</strong></span>
                  <span>FRAME <strong>STATIC / MOCK</strong></span>
                </div>
              </SpatialOverlay>
            )
          ) : null}
        </ContextLayerStack>
      </SceneTransitionController>

      <CoreDock position={corePosition} coreState={coreState} visualPhase={visualRuntime.phase} />
      <div className="spatial-proof-badge">TEST/MOCK — NOT RUNTIME ACCEPTANCE</div>
      <div className="sr-only">TEST/MOCK adaptive spatial canvas — not runtime acceptance</div>
    </main>
  );
}
