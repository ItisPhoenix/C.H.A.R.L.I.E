'use client'

import { cn } from '@/lib/utils'

type ButtonVariant = 'primary' | 'danger' | 'ghost'
type ButtonSize = 'sm' | 'md'

const variantStyles: Record<ButtonVariant, string> = {
  primary: 'bg-charlie-cyan/20 text-charlie-cyan border-charlie-cyan/30 hover:bg-charlie-cyan/30',
  danger: 'bg-charlie-red/20 text-charlie-red border-charlie-red/30 hover:bg-charlie-red/30',
  ghost: 'bg-transparent text-charlie-dim border-charlie-border hover:text-charlie-text hover:border-charlie-cyan/30',
}

const sizeStyles: Record<ButtonSize, string> = {
  sm: 'px-2 py-1 text-xs',
  md: 'px-4 py-2 text-sm',
}

interface ButtonProps {
  variant?: ButtonVariant
  size?: ButtonSize
  loading?: boolean
  disabled?: boolean
  onClick?: (e: React.MouseEvent) => void
  children: React.ReactNode
  className?: string
}

export function Button({
  variant = 'primary',
  size = 'md',
  loading,
  disabled,
  onClick,
  children,
  className,
}: ButtonProps) {
  return (
    <button
      onClick={onClick}
      disabled={disabled || loading}
      className={cn(
        'inline-flex items-center justify-center gap-2 font-medium rounded-lg border transition-colors duration-200',
        'disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer',
        variantStyles[variant],
        sizeStyles[size],
        className,
      )}
    >
      {loading && (
        <svg className="animate-spin h-3 w-3" viewBox="0 0 24 24">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
        </svg>
      )}
      {children}
    </button>
  )
}
