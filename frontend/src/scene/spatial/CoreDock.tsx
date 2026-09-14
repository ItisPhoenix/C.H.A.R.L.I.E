import type { ReactElement } from "react";
import { CharlieCore } from "../CharlieCore";
import type { CorePosition } from "../sceneState";
import type { VisualRuntimePhase } from "../../runtime/visualRuntime";

interface CoreDockProps {
  position: CorePosition;
  coreState: string;
  visualPhase: VisualRuntimePhase;
}

export function CoreDock({ position, coreState, visualPhase }: CoreDockProps): ReactElement {
  return (
    <div className="spatial-core-dock" data-core-placement={position}>
      <CharlieCore position={position} coreState={coreState} visualPhase={visualPhase} />
    </div>
  );
}
