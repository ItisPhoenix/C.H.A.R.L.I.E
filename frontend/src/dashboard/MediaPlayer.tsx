import type { ReactElement } from "react";
import { Panel } from "./Panel";

export function MediaPlayer(): ReactElement {
  return (
    <Panel id="media" title="Media Player">
      <p className="media-unavailable">Media playback is unavailable.</p>
    </Panel>
  );
}
