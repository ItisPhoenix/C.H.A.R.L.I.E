import type { ReactElement } from "react";

export function Background(): ReactElement {
  return (
    <div className="hud-background" aria-hidden="true">
      <div className="hud-grid" />
      <svg className="hud-circuitry" viewBox="0 0 1536 1024" preserveAspectRatio="none">
        <g>
          <path d="M0 251h51l17-18h117l20 20h145M0 608h79l20-20h198l24 24h95M0 850h120l35-35h220" />
          <path d="M1536 177h-92l-21 21h-139M1536 352h-80l-25-25h-167M1536 735h-70l-28-28h-169M1536 881h-98l-20-20h-133" />
          <path d="M198 68v69l35 35v127M1315 68v71l-38 38v123M394 1024v-70l35-35v-81M1113 1024v-77l-32-32v-90" />
        </g>
        <g className="circuit-dots">
          <circle cx="51" cy="251" r="2" /><circle cx="185" cy="233" r="2" /><circle cx="321" cy="612" r="2" />
          <circle cx="1444" cy="177" r="2" /><circle cx="1264" cy="327" r="2" /><circle cx="1438" cy="707" r="2" />
          <circle cx="429" cy="919" r="2" /><circle cx="1081" cy="915" r="2" />
        </g>
      </svg>
      <div className="hud-vignette" />
      <div className="hud-noise" />
    </div>
  );
}
