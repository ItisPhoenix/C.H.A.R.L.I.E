import { Component, type ErrorInfo, type ReactElement, type ReactNode } from "react";
import {
  validateSurfaceSpec,
  type ActionSpec,
  type SurfaceSpec,
} from "./surfaceSchema";
import { PrimitiveNode } from "./primitives/PrimitiveNode";
import { LayoutContainer } from "./primitives/LayoutContainer";
import { ActionPrimitive } from "./primitives/ActionPrimitive";
import { sendCommand } from "../runtime/bridge";

interface ErrorBoundaryProps {
  children: ReactNode;
  fallbackTitle?: string;
}

interface ErrorBoundaryState {
  hasError: boolean;
  errorMessage: string;
}

class SurfaceErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  constructor(props: ErrorBoundaryProps) {
    super(props);
    this.state = { hasError: false, errorMessage: "" };
  }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { hasError: true, errorMessage: error.message };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("[SurfaceComposer] Render error caught in boundary:", error, info);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="p-4 rounded-xl border border-rose-500/30 bg-rose-950/20 text-left my-2 text-rose-200 text-xs font-mono">
          <div className="font-bold mb-1">
            [Surface Error: {this.props.fallbackTitle || "Rendering failed"}]
          </div>
          <div className="text-slate-400 text-[11px]">{this.state.errorMessage}</div>
        </div>
      );
    }
    return this.props.children;
  }
}

export interface SurfaceComposerProps {
  spec: SurfaceSpec | Record<string, unknown>;
  onAction?: (action: ActionSpec) => void;
  className?: string;
}

export function SurfaceComposer({
  spec: rawSpec,
  onAction,
  className = "",
}: SurfaceComposerProps): ReactElement {
  // Validate schema
  const validation = validateSurfaceSpec(rawSpec);

  if (!validation.valid || !validation.spec) {
    return (
      <div className="p-4 rounded-xl border border-amber-500/40 bg-slate-950/80 text-left my-2 text-xs font-mono text-amber-200">
        <div className="font-bold mb-1">⚠️ Surface Schema Validation Error</div>
        <ul className="list-disc pl-4 text-[11px] text-slate-300 flex flex-col gap-0.5">
          {validation.errors.map((err, idx) => (
            <li key={idx}>{err}</li>
          ))}
        </ul>
      </div>
    );
  }

  const spec = validation.spec;

  const handleAction = (action: ActionSpec) => {
    // 1. Emit semantic surface_action event via bridge
    try {
      sendCommand("surface_action", {
        surface_id: spec.surface_id,
        action_id: action.action_id,
        revision: spec.revision,
        payload: action.payload || {},
      });
    } catch {
      // Ignore bridge errors during disconnected or test modes
    }

    // 2. Call local callback if provided
    if (onAction) {
      onAction(action);
    }
  };

  return (
    <SurfaceErrorBoundary fallbackTitle={spec.title}>
      <div
        className={`charlie-composed-surface flex flex-col gap-3 w-full text-left select-text ${className}`}
        data-surface-id={spec.surface_id}
        data-surface-revision={spec.revision}
      >
        {/* Content Layout */}
        <LayoutContainer layout={spec.layout}>
          {spec.primitives.map((primitive, idx) => (
            <div key={primitive.id || `prim_${idx}`}>
              <PrimitiveNode primitive={primitive} onAction={handleAction} />
            </div>
          ))}
        </LayoutContainer>

        {/* Action Controls Bar */}
        {spec.actions && spec.actions.length > 0 && (
          <div className="flex flex-wrap items-center gap-2 pt-2 border-t border-cyan-500/15 mt-1">
            {spec.actions.map((act) => (
              <ActionPrimitive
                key={act.id}
                action={act}
                onAction={handleAction}
              />
            ))}
          </div>
        )}
      </div>
    </SurfaceErrorBoundary>
  );
}
