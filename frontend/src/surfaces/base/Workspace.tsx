import type { ReactElement } from "react";
import type { SurfaceSpec } from "../../store/charlie";
import { Frame } from "./Frame";
import { StreamingMarkdown } from "../../components/StreamingMarkdown";

export function Workspace({ spec }: { spec: SurfaceSpec }): ReactElement {
  return (
    <Frame spec={spec} showRationale={false}>
      <div className="flex-1 overflow-y-auto">
        <StreamingMarkdown content={spec.rationale} />
      </div>
    </Frame>
  );
}
