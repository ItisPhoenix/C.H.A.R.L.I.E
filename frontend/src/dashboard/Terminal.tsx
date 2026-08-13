import type { ReactElement } from "react";
import { Panel } from "./Panel";

export function Terminal(): ReactElement {
  return (
    <Panel id="terminal" title="Terminal">
      <p className="terminal-unavailable">Terminal output is unavailable.</p>
    </Panel>
  );
}
