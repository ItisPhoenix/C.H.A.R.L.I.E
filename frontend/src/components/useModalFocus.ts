import { useEffect, useRef, type RefObject } from "react";

const FOCUSABLE = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "[tabindex]:not([tabindex=\"-1\"])",
].join(",");

export function useModalFocus<T extends HTMLElement>(open: boolean, onClose: () => void): RefObject<T | null> {
  const dialogRef = useRef<T | null>(null);
  const closeRef = useRef(onClose);

  useEffect(() => {
    closeRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    if (!open) return;

    const dialog = dialogRef.current;
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const focusable = () => Array.from(dialog?.querySelectorAll<HTMLElement>(FOCUSABLE) ?? []);
    const first = focusable()[0];
    (first || dialog)?.focus();

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        closeRef.current();
        return;
      }
      if (event.key !== "Tab") return;

      const elements = focusable();
      if (elements.length === 0) {
        event.preventDefault();
        dialog?.focus();
        return;
      }

      const current = document.activeElement;
      const index = elements.indexOf(current as HTMLElement);
      const next = event.shiftKey
        ? elements[index <= 0 ? elements.length - 1 : index - 1]
        : elements[index === elements.length - 1 ? 0 : index + 1];
      event.preventDefault();
      next?.focus();
    };

    dialog?.addEventListener("keydown", handleKeyDown);
    return () => {
      dialog?.removeEventListener("keydown", handleKeyDown);
      previous?.focus();
    };
  }, [open]);

  return dialogRef;
}
