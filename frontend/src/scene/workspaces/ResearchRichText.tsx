import type { ReactElement } from "react";

function normalizeLines(value: string): string[] {
  return value
    .replace(/\s+(?=#{1,3}\s)/g, "\n")
    .replace(/\s+-\s+/g, "\n- ")
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
}

function inlineText(value: string): ReactElement[] {
  const tokens = value.split(/(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\([^\)]+\))/g).filter(Boolean);
  return tokens.map((token, index) => {
    if (token.startsWith("**") && token.endsWith("**")) {
      return <strong key={index} className="text-cyan-100 font-semibold">{token.slice(2, -2)}</strong>;
    }
    if (token.startsWith("`") && token.endsWith("`")) {
      return <code key={index} className="text-cyan-200 bg-cyan-950/50 px-1 rounded">{token.slice(1, -1)}</code>;
    }
    const link = token.match(/^\[([^\]]+)\]\(([^\)]+)\)$/);
    if (link) {
      return <span key={index} className="text-cyan-300">{link[1]}</span>;
    }
    return <span key={index}>{token}</span>;
  });
}

export function ResearchRichText({ text, className = "" }: { text: string; className?: string }): ReactElement {
  const lines = normalizeLines(text);
  return (
    <div className={`research-rich-text space-y-3 ${className}`.trim()}>
      {lines.map((line, index) => {
        const heading = line.match(/^(#{1,3})\s+(.+)$/);
        if (heading) {
          return (
            <h3 key={index} className="text-sm font-mono font-bold tracking-[0.12em] text-cyan-300 uppercase pt-2">
              {inlineText(heading[2])}
            </h3>
          );
        }
        if (line.startsWith("- ")) {
          return (
            <div key={index} className="flex gap-3 text-sm text-slate-200 font-sans leading-relaxed">
              <span className="text-cyan-400 font-mono">—</span>
              <p>{inlineText(line.slice(2))}</p>
            </div>
          );
        }
        return <p key={index} className="text-base text-slate-100 font-sans leading-8">{inlineText(line)}</p>;
      })}
    </div>
  );
}
