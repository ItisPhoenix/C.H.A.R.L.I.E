import { useState, useRef, useEffect, type ReactElement } from "react";
import type { WorkspaceInstance } from "../../layout/workspaceStore";
import { useCharlieStore } from "../../store/charlie";

export function ConversationWorkspace({ workspace }: { workspace: WorkspaceInstance }): ReactElement {
  const chatMessages = useCharlieStore((s) => s.chatMessages);
  const timeline = Array.isArray(chatMessages) ? chatMessages : [];
  const coreState = useCharlieStore((s) => s.coreState);
  const isExecuting = coreState === "thinking" || coreState === "working";
  const [inputVal, setInputVal] = useState("");
  const endRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (typeof endRef.current?.scrollIntoView === "function") {
      endRef.current.scrollIntoView({ behavior: "smooth" });
    }
  }, [timeline]);

  return (
    <div className="w-full h-full flex flex-col justify-between font-mono select-none text-left p-2 overflow-hidden space-y-3">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-cyan-500/20 pb-2">
        <div className="flex items-center gap-2">
          <div className="w-2.5 h-2.5 rounded-full bg-cyan-400" />
          <span className="text-xs font-bold text-cyan-300 tracking-wider">
            CONVERSATION & DIALOGUE LOG
          </span>
        </div>
        <div className="text-[10px] text-slate-400">
          SESSION ID: {workspace.id}
        </div>
      </div>

      {/* Message Stream */}
      <div className="flex-1 w-full p-4 rounded-xl border border-cyan-500/20 bg-slate-950/70 backdrop-blur-md overflow-y-auto space-y-4">
        {timeline.length === 0 ? (
          <div className="text-xs text-slate-500 italic">No conversation messages yet.</div>
        ) : (
          timeline.map((msg, idx) => {
            const isUser = msg.role === "user";
            return (
              <div
                key={idx}
                className={`flex flex-col gap-1 max-w-[85%] ${
                  isUser ? "ml-auto items-end" : "mr-auto items-start"
                }`}
              >
                <span className="text-[9px] text-cyan-400/70 uppercase">
                  {isUser ? "OPERATOR" : "C.H.A.R.L.I.E."}
                </span>
                <div
                  className={`p-3 rounded-xl text-xs leading-relaxed font-sans ${
                    isUser
                      ? "bg-cyan-950/70 border border-cyan-400/40 text-cyan-100"
                      : "bg-slate-900/80 border border-cyan-500/20 text-slate-200"
                  }`}
                >
                  {msg.text}
                </div>
              </div>
            );
          })
        )}
        <div ref={endRef} />
      </div>

      {/* Input bar */}
      <div className="p-2 rounded-xl border border-cyan-500/25 bg-slate-950/90 flex items-center gap-3">
        <input
          type="text"
          value={inputVal}
          onChange={(e) => setInputVal(e.target.value)}
          placeholder="Send prompt to Charlie..."
          className="flex-1 bg-transparent border-none outline-none text-xs text-slate-200 placeholder-slate-500 font-sans focus:ring-0"
        />
        <button
          type="button"
          disabled={isExecuting || !inputVal.trim()}
          className="px-3 py-1 text-xs font-bold rounded-lg bg-cyan-950 text-cyan-300 border border-cyan-500/40 hover:bg-cyan-900 transition disabled:opacity-40 cursor-pointer"
        >
          Send
        </button>
      </div>
    </div>
  );
}
