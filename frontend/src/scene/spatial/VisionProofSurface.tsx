import type { ReactElement } from "react";

export interface VisionProofBox {
  id: string;
  label: string;
  confidence: number;
  box: [number, number, number, number];
  color: string;
}

const MOCK_FRAME_SOURCE = `data:image/svg+xml,${encodeURIComponent(`
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1600 900">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#123947"/><stop offset="0.42" stop-color="#081c29"/><stop offset="1" stop-color="#02060c"/>
    </linearGradient>
    <linearGradient id="floor" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#0c2b36" stop-opacity=".66"/><stop offset="1" stop-color="#02060b" stop-opacity=".94"/>
    </linearGradient>
    <linearGradient id="console" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#1b5360"/><stop offset=".42" stop-color="#0d2936"/><stop offset="1" stop-color="#06111b"/>
    </linearGradient>
    <linearGradient id="signal-core" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#77e7f5" stop-opacity=".8"/><stop offset=".22" stop-color="#1b7689" stop-opacity=".48"/><stop offset="1" stop-color="#06121b" stop-opacity=".9"/>
    </linearGradient>
    <radialGradient id="signal" cx="50%" cy="42%" r="60%">
      <stop offset="0" stop-color="#1c6570" stop-opacity=".38"/><stop offset=".55" stop-color="#0b303b" stop-opacity=".12"/><stop offset="1" stop-color="#031019" stop-opacity="0"/>
    </radialGradient>
    <pattern id="scanlines" width="8" height="8" patternUnits="userSpaceOnUse">
      <path d="M0 0H8" stroke="#b2f5ff" stroke-opacity=".06" stroke-width="1"/>
    </pattern>
    <filter id="soft-glow" x="-40%" y="-40%" width="180%" height="180%">
      <feGaussianBlur stdDeviation="12" result="blur"/><feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>
  </defs>
  <rect width="1600" height="900" fill="url(#bg)"/>
  <rect width="1600" height="900" fill="url(#signal)"/>
  <path d="M0 520H1600V900H0Z" fill="url(#floor)"/>
  <g fill="none" stroke="#64d9eb" stroke-opacity=".14" stroke-width="2">
    <path d="M0 182H1600M0 344H1600M0 506H1600M0 668H1600M0 830H1600"/>
    <path d="M120 0V900M320 0V900M520 0V900M720 0V900M920 0V900M1120 0V900M1320 0V900M1520 0V900"/>
    <path d="M800 520L80 900M800 520L360 900M800 520L620 900M800 520L980 900M800 520L1240 900M800 520L1520 900" stroke-opacity=".2"/>
  </g>
  <g fill="none" stroke="#8de9f7" stroke-opacity=".22" stroke-width="3">
    <path d="M80 520H1520"/><path d="M200 554H1400"/><path d="M320 606H1280"/><path d="M470 676H1130"/>
  </g>

  <g transform="translate(132 168)">
    <rect width="418" height="306" rx="14" fill="#06131d" fill-opacity=".9" stroke="#68d9eb" stroke-opacity=".72" stroke-width="4"/>
    <rect x="22" y="24" width="374" height="216" rx="8" fill="url(#console)" stroke="#8de9f7" stroke-opacity=".32" stroke-width="3"/>
    <path d="M48 76H370M48 122H290M48 168H338" stroke="#a2eff9" stroke-opacity=".48" stroke-width="5"/>
    <path d="M48 202H190M218 202H370" stroke="#40cbe3" stroke-opacity=".72" stroke-width="3"/>
    <g fill="#bff7ff" fill-opacity=".76">
      <circle cx="56" cy="48" r="5"/><circle cx="76" cy="48" r="5"/><circle cx="96" cy="48" r="5"/>
    </g>
    <path d="M42 260H376" stroke="#51d7ea" stroke-opacity=".4" stroke-width="2"/>
    <g fill="#8feaf5" fill-opacity=".58" font-family="monospace" font-size="14" letter-spacing="3">
      <text x="28" y="286">CONSOLE A-17</text><text x="296" y="286">LINK 94%</text>
    </g>
  </g>

  <g transform="translate(0 0)">
    <ellipse cx="840" cy="406" rx="252" ry="164" fill="#39c7dc" fill-opacity=".08" filter="url(#soft-glow)"/>
    <path d="M620 470L674 214L1012 214L1060 470Z" fill="#071822" fill-opacity=".8" stroke="#70e0ee" stroke-opacity=".5" stroke-width="3"/>
    <path d="M692 448L720 244H968L1000 448Z" fill="url(#signal-core)" fill-opacity=".66"/>
    <g fill="none" stroke="#a9f4ff" stroke-opacity=".56">
      <ellipse cx="844" cy="244" rx="124" ry="24" stroke-width="4"/>
      <ellipse cx="844" cy="448" rx="154" ry="28" stroke-width="4"/>
      <circle cx="844" cy="336" r="54" stroke-width="3"/><circle cx="844" cy="336" r="92" stroke-width="2" stroke-dasharray="11 18"/>
      <path d="M844 250V422M754 336H934" stroke-width="3" stroke-dasharray="5 12"/>
    </g>
    <g fill="#c5f8ff" fill-opacity=".8">
      <circle cx="748" cy="288" r="5"/><circle cx="932" cy="388" r="5"/><circle cx="844" cy="336" r="7"/>
    </g>
    <g fill="#9deef8" fill-opacity=".62" font-family="monospace" font-size="14" letter-spacing="3">
      <text x="706" y="194">ARRAY B-04</text><text x="876" y="488">SIGNAL 89%</text>
    </g>
  </g>

  <g transform="translate(1144 360)">
    <path d="M42 48L318 48L374 332H0Z" fill="#06121b" stroke="#f2c74b" stroke-opacity=".72" stroke-width="4"/>
    <path d="M72 78L288 78L324 274H38Z" fill="url(#console)" stroke="#a6ecf7" stroke-opacity=".44" stroke-width="3"/>
    <path d="M90 142H270M100 184H232M110 226H286" stroke="#b5f3fb" stroke-opacity=".5" stroke-width="5"/>
    <path d="M74 294H306" stroke="#f7d365" stroke-opacity=".72" stroke-width="3"/>
    <g fill="#f7d365" fill-opacity=".8"><circle cx="94" cy="108" r="5"/><circle cx="114" cy="108" r="5"/><circle cx="134" cy="108" r="5"/></g>
    <g fill="#f4d777" fill-opacity=".72" font-family="monospace" font-size="14" letter-spacing="3">
      <text x="30" y="360">TERMINAL C-32</text><text x="270" y="360">86%</text>
    </g>
  </g>

  <rect width="1600" height="900" fill="url(#scanlines)" opacity=".36"/>
  <g fill="#d8f8ff" font-family="monospace" font-size="24" letter-spacing="4">
    <text x="72" y="82">TEST / MOCK VISION FRAME</text>
    <text x="72" y="120" fill="#65d7f1" font-size="16">LOCAL GROUNDING / STATIC MEDIA FIXTURE</text>
    <text x="1240" y="82" fill="#65d7f1" font-size="16">FRAME 001 · SYNTHETIC</text>
  </g>
  <g fill="#9cecf7" fill-opacity=".6" font-family="monospace" font-size="14" letter-spacing="2">
    <text x="72" y="848">MEDIA FIELD / OBSERVED SURFACES</text><text x="1320" y="848">NO DEVICE INPUT</text>
  </g>
</svg>`)}`;

interface VisionProofSurfaceProps {
  boxes: readonly VisionProofBox[];
  onSelectBox: (box: VisionProofBox) => void;
}

export function VisionProofSurface({ boxes, onSelectBox }: VisionProofSurfaceProps): ReactElement {
  return (
    <div className="spatial-vision-surface">
      <div className="spatial-vision-frame">
        <img src={MOCK_FRAME_SOURCE} alt="TEST/MOCK local vision frame" draggable={false} />
        {boxes.map((box) => {
          const [ymin, xmin, ymax, xmax] = box.box;
          return (
            <button
              key={box.id}
              type="button"
              className="spatial-vision-box"
              style={{
                top: `${ymin}%`,
                left: `${xmin}%`,
                width: `${xmax - xmin}%`,
                height: `${ymax - ymin}%`,
                borderColor: box.color,
              }}
              onClick={() => onSelectBox(box)}
              aria-label={`Select ${box.label}`}
            >
              <span style={{ backgroundColor: box.color, color: "#020710" }}>
                {box.label} · {(box.confidence * 100).toFixed(0)}%
              </span>
            </button>
          );
        })}
      </div>
      <div className="spatial-vision-meta">
        <span>VISION / MEDIA FIELD</span>
        <span>STATIC FRAME · GROUNDING AVAILABLE</span>
      </div>
    </div>
  );
}
