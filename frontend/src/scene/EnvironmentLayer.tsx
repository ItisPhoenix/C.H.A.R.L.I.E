import type { ReactElement } from "react";
import type { CorePosition } from "./sceneState";

interface EnvironmentLayerProps {
  corePosition: CorePosition;
  hasWorkspace: boolean;
}

export function EnvironmentLayer({ corePosition, hasWorkspace }: EnvironmentLayerProps): ReactElement {
  const lightX = hasWorkspace ? "60%" : corePosition === "dock_bottom_right" ? "85%" : "50%";
  const lightY = hasWorkspace ? "45%" : corePosition === "dock_bottom_right" ? "85%" : "50%";

  return (
    <div
      className="charlie-env-layer"
      aria-hidden="true"
      style={
        {
          "--light-x": lightX,
          "--light-y": lightY,
        } as React.CSSProperties
      }
    >
      {/* 1. Base dark navy / near-black radial background */}
      <div className="charlie-env-base" />

      {/* 2. Dual minor + major technical grid */}
      <div className="charlie-env-grid" />

      {/* 3. Restrained perimeter & corner technical framing */}
      <div className="charlie-env-frame">
        <div className="charlie-frame-corner charlie-frame-tl" />
        <div className="charlie-frame-corner charlie-frame-tr" />
        <div className="charlie-frame-corner charlie-frame-bl" />
        <div className="charlie-frame-corner charlie-frame-br" />
        
        {/* Top & Bottom central sci-fi brackets */}
        <svg className="charlie-frame-bracket-top" viewBox="0 0 140 12" fill="none">
          <path d="M 0 0 L 25 0 L 35 8 L 105 8 L 115 0 L 140 0" stroke="rgba(0, 240, 255, 0.45)" strokeWidth="1.2" />
        </svg>
        <svg className="charlie-frame-bracket-bottom" viewBox="0 0 140 12" fill="none">
          <path d="M 0 12 L 25 12 L 35 4 L 105 4 L 115 12 L 140 12" stroke="rgba(0, 240, 255, 0.45)" strokeWidth="1.2" />
        </svg>

        {/* Perimeter edge tick marks */}
        <div className="charlie-frame-ticks-x-top" />
        <div className="charlie-frame-ticks-x-bottom" />
        <div className="charlie-frame-ticks-y-left" />
        <div className="charlie-frame-ticks-y-right" />
      </div>

      {/* 4. Deep 4-edge & corner vignette */}
      <div className="charlie-env-vignette" />

      {/* 5. Restrained subtle grain texture */}
      <div className="charlie-env-noise" />
    </div>
  );
}
