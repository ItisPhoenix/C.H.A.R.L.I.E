import type { ReactElement, ReactNode } from "react";

// Ported from frontend@c7aa7df~1's ChatView.tsx formatMessageContent -- hand-rolled, not a markdown library, kept as-is.
function parseInlineFormatting(text: string): ReactNode[] {
  const boldRegex = /(\*\*.*?\*\*|`.*?`)/g;
  const parts = text.split(boldRegex);
  return parts.map((part, index) => {
    if (part.startsWith("**") && part.endsWith("**")) {
      return (
        <strong key={index} className="font-bold text-slate-100">
          {part.slice(2, -2)}
        </strong>
      );
    }
    if (part.startsWith("`") && part.endsWith("`")) {
      return (
        <code key={index} className="px-1 py-0.5 rounded bg-white/5 border border-white/10 font-mono text-xs text-cyan-300">
          {part.slice(1, -1)}
        </code>
      );
    }
    return part;
  });
}

function parseInlineMarkdown(text: string): ReactNode {
  const paragraphs = text.split("\n");
  return (
    <div className="space-y-2">
      {paragraphs.map((p, idx) => {
        const trimmed = p.trim();
        if (trimmed.startsWith("- ") || trimmed.startsWith("* ")) {
          return (
            <ul key={idx} className="list-disc list-inside pl-2 space-y-0.5">
              <li className="text-[14px] leading-relaxed text-slate-200">{parseInlineFormatting(trimmed.substring(2))}</li>
            </ul>
          );
        }
        return (
          <p key={idx} className="text-[14px] leading-relaxed text-slate-200">
            {parseInlineFormatting(p)}
          </p>
        );
      })}
    </div>
  );
}

export function StreamingMarkdown({ content }: { content: string }): ReactElement | null {
  if (!content) return null;
  const parts = content.split("```");
  if (parts.length < 2) return <>{parseInlineMarkdown(content)}</>;

  return (
    <div className="space-y-3 font-sans">
      {parts.map((part, index) => {
        if (index % 2 === 1) {
          const lines = part.split("\n");
          const language = lines[0].trim() || "code";
          const codeText = lines.slice(1).join("\n").trim();
          return (
            <div key={index} className="rounded-lg overflow-hidden border border-white/10 bg-zinc-950 font-mono text-xs my-2">
              <div className="px-3 py-1.5 bg-white/[0.03] border-b border-white/5 text-[10px] text-slate-400 font-sans tracking-wide uppercase select-none">
                {language}
              </div>
              <pre className="p-3 overflow-x-auto text-[#f4f6fa] leading-relaxed">
                <code>{codeText}</code>
              </pre>
            </div>
          );
        }
        return <div key={index}>{parseInlineMarkdown(part)}</div>;
      })}
    </div>
  );
}
