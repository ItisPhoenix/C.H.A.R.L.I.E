"use client";

import type { ButtonHTMLAttributes, ReactElement } from "react";

type ButtonVariant = "neutral" | "accent" | "danger" | "success";
type ButtonSize = "md" | "sm";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
}

const VARIANT_CLASSES: Record<ButtonVariant, string> = {
  neutral:
    "border border-white/10 bg-zinc-900/50 text-slate-300 hover:text-white hover:bg-zinc-900",
  accent:
    "border border-transparent bg-accent text-[#03151a] hover:brightness-110 disabled:bg-transparent disabled:text-slate-500 disabled:border-white/10",
  danger:
    "border border-red-500/20 bg-red-950/40 text-red-400 hover:bg-red-950/60",
  success:
    "border border-emerald-500/20 bg-emerald-950/40 text-emerald-400 hover:bg-emerald-950/60",
};

const SIZE_CLASSES: Record<ButtonSize, string> = {
  md: "px-3 py-1.5 text-xs",
  sm: "px-2 py-1 text-[10px]",
};

/** Shared button styling -- one radius, one active-press scale, one set of variant colors across the dashboard. */
export function Button({
  variant = "neutral",
  size = "md",
  className = "",
  children,
  ...rest
}: ButtonProps): ReactElement {
  return (
    <button
      className={`rounded-lg flex items-center gap-1.5 cursor-pointer active:scale-[0.98] transition disabled:opacity-40 disabled:cursor-not-allowed ${VARIANT_CLASSES[variant]} ${SIZE_CLASSES[size]} ${className}`}
      {...rest}
    >
      {children}
    </button>
  );
}
