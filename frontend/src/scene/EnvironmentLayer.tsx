import type { ReactElement } from "react";
import type { CorePosition } from "./sceneState";

interface EnvironmentLayerProps {
  corePosition: CorePosition;
  hasWorkspace: boolean;
}

export function EnvironmentLayer({ corePosition, hasWorkspace }: EnvironmentLayerProps): ReactElement {
  // Biasing illumination toward core or workspace
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
        <div className="charlie-frame-tick-top">SYS // 01.SPATIAL.HUD</div>
      </div>

      {/* 4. Deep 4-edge & corner vignette */}
      <div className="charlie-env-vignette" />

      {/* 5. Restrained subtle grain texture */}
      <div className="charlie-env-noise" />
    </div>
  );
}
