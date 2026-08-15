import { useState, type ReactElement } from "react";
import type { PrimitiveSpec } from "../surfaceSchema";

export function ImagePrimitive({ primitive }: { primitive: PrimitiveSpec }): ReactElement {
  const data = primitive.data || {};
  const src = String(data.src ?? "");
  const alt = String(data.alt ?? "Surface media");
  const caption = data.caption ? String(data.caption) : null;
  const [hasError, setHasError] = useState(false);

  // Security Scheme Check
  const isSafeScheme =
    src.startsWith("https://") ||
    src.startsWith("http://") ||
    src.startsWith("data:image/") ||
    src.startsWith("/");

  if (!isSafeScheme || hasError) {
    return (
      <div className="w-full p-4 rounded-xl border border-cyan-500/20 bg-slate-950/40 text-center text-xs text-slate-500 my-2">
        <span>[Image unavailable or restricted]</span>
      </div>
    );
  }

  return (
    <figure className="w-full my-2.5 rounded-xl overflow-hidden border border-cyan-500/20 bg-slate-950/60">
      <img
        src={src}
        alt={alt}
        className="w-full max-h-64 object-cover"
        loading="lazy"
        onError={() => setHasError(true)}
      />
      {caption && (
        <figcaption className="p-2 text-[11px] text-slate-400 font-mono border-t border-cyan-500/10">
          {caption}
        </figcaption>
      )}
    </figure>
  );
}
