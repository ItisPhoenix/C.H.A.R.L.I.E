import { useRef } from "react";

export interface HudControlsProps {
  pttDisabled: boolean;
  conversationDisabled: boolean;
  pressed: boolean;
  status?: string;
  onPttStart: () => void;
  onPttStop: () => void;
  onPttCancel: () => void;
  onOpenConversation: () => void;
}

export function HudControls({
  pttDisabled,
  conversationDisabled,
  pressed,
  status,
  onPttStart,
  onPttStop,
  onPttCancel,
  onOpenConversation,
}: HudControlsProps) {
  const pressedRef = useRef(false);
  const start = () => {
    if (pttDisabled || pressedRef.current) return;
    pressedRef.current = true;
    onPttStart();
  };
  const stop = () => {
    if (!pressedRef.current) return;
    pressedRef.current = false;
    onPttStop();
  };
  const cancel = () => {
    if (!pressedRef.current) return;
    pressedRef.current = false;
    onPttCancel();
  };

  return (
    <div className="utility-strip utility-strip--runtime" data-utility-strip>
      <button
        type="button"
        className="utility-control utility-control--ptt"
        aria-label={pttDisabled ? "Push to talk unavailable" : "Hold to talk"}
        title={pttDisabled ? "Push to talk unavailable" : "Hold to talk"}
        disabled={pttDisabled}
        data-pressed={pressed || undefined}
        onPointerDown={start}
        onPointerUp={stop}
        onPointerLeave={stop}
        onPointerCancel={cancel}
        onKeyDown={event => {
          if ((event.key === " " || event.key === "Enter") && !event.repeat) {
            event.preventDefault();
            start();
          }
        }}
        onKeyUp={event => {
          if (event.key === " " || event.key === "Enter") {
            event.preventDefault();
            stop();
          }
        }}
      >
        <svg viewBox="0 0 22 22" role="presentation" aria-hidden="true">
          <circle cx="11" cy="11" r="7" />
          <path d="M11 7.5v7M7.5 11h7" />
        </svg>
      </button>
      <span className="utility-divider-line" aria-hidden="true" />
      <button
        type="button"
        className="utility-control utility-control--conversation"
        aria-label={conversationDisabled ? "Open conversation unavailable" : "Open conversation"}
        title={conversationDisabled ? "Open conversation unavailable while runtime is disconnected" : "Open conversation"}
        disabled={conversationDisabled}
        onClick={onOpenConversation}
      >
        <svg viewBox="0 0 22 22" role="presentation" aria-hidden="true">
          <path d="M4 5.5h14v9H9l-4 3v-3H4z" />
          <path d="M7.5 9h7M7.5 12h4" />
        </svg>
      </button>
      <span className="utility-status" aria-hidden="true"><i /><i /><i /></span>
      {status && <span className="utility-feedback" role="status" aria-live="polite">{status}</span>}
    </div>
  );
}
