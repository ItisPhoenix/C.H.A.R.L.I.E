import type { ReactElement, ReactNode } from "react";

interface ModalProps {
  children: ReactNode;
  labelledBy: string;
  accent?: string;
}

// Shared shell for interruption dialogs (recovery proposals, tool approvals). Ported from frontend@c7aa7df~1.
export function Modal({ children, labelledBy, accent = "info" }: ModalProps): ReactElement {
  return (
    <div className="w-full h-full flex flex-col items-center justify-center">
      <div role="dialog" aria-modal="true" aria-labelledby={labelledBy} className={`w-full max-w-lg glass p-6 flex flex-col gap-5 role-${accent}`}>
        {children}
      </div>
    </div>
  );
}

interface ModalFieldProps {
  label: string;
  tone?: "error" | "success" | "warning" | "muted";
  children: ReactNode;
}

const TONE_COLOR: Record<string, string> = {
  error: "var(--color-status-error)",
  success: "var(--color-status-success)",
  warning: "var(--color-status-warning)",
  muted: "var(--color-text-secondary)",
};

export function ModalField({ label, tone = "muted", children }: ModalFieldProps): ReactElement {
  return (
    <div>
      <span className="text-xs uppercase tracking-wider font-semibold" style={{ color: TONE_COLOR[tone] }}>
        {label}
      </span>
      <div className="mt-1 text-sm text-[var(--color-text-secondary)]">{children}</div>
    </div>
  );
}

interface ModalActionsProps {
  rejectLabel: string;
  approveLabel: string;
  onReject: () => void;
  onApprove: () => void;
  busy?: boolean;
}

export function ModalActions({ rejectLabel, approveLabel, onReject, onApprove, busy }: ModalActionsProps): ReactElement {
  return (
    <div className="flex justify-end gap-3 mt-2">
      <button
        onClick={onReject}
        disabled={busy}
        className="px-4 py-2 text-sm font-medium rounded-xl border border-[var(--color-status-error)]/30 hover:border-[var(--color-status-error)] text-[var(--color-status-error)] hover:bg-[var(--color-status-error-dim)] transition cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
      >
        {rejectLabel}
      </button>
      <button
        onClick={onApprove}
        disabled={busy}
        className="px-4 py-2 text-sm font-medium rounded-xl bg-[var(--color-status-success)] hover:brightness-110 text-white transition cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
      >
        {approveLabel}
      </button>
    </div>
  );
}
