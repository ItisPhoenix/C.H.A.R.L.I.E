'use client'

import { cn } from '@/lib/utils'
import { Loader2 } from 'lucide-react'

type ButtonVariant = 'primary' | 'danger' | 'ghost' | 'success' | 'warning'
type ButtonSize = 'xs' | 'sm' | 'md' | 'lg'

const variantStyles: Record<ButtonVariant, string> = {
  primary:
    'bg-charlie-cyan/10 text-charlie-cyan border-charlie-cyan/10 hover:bg-charlie-cyan/20 hover:border-charlie-cyan/20',
  danger:
    'bg-charlie-red/10 text-charlie-red border-charlie-red/10 hover:bg-charlie-red/20 hover:border-charlie-red/20',
  ghost:
    'bg-transparent text-charlie-dim border-transparent hover:text-charlie-text hover:bg-charlie-text/5',
  success:
    'bg-charlie-green/10 text-charlie-green border-charlie-green/10 hover:bg-charlie-green/20 hover:border-charlie-green/20',
  warning:
    'bg-charlie-amber/10 text-charlie-amber border-charlie-amber/10 hover:bg-charlie-amber/20 hover:border-charlie-amber/20',
}

const sizeStyles: Record<ButtonSize, string> = {
  xs: 'px-2.5 py-1 text-[11px]',
  sm: 'px-3.5 py-1.5 text-xs',
  md: 'px-4 py-2 text-sm',
  lg: 'px-6 py-2.5 text-base',
}

interface ButtonProps {
  variant?: ButtonVariant
  size?: ButtonSize
  loading?: boolean
  disabled?: boolean
  onClick?: (e: React.MouseEvent) => void
  children: React.ReactNode
  className?: string
  title?: string
}

export function Button({
  variant = 'primary',
  size = 'md',
  loading,
  disabled,
  onClick,
  children,
  className,
  title,
}: ButtonProps) {
  return (
    <button
      onClick={onClick}
      disabled={disabled || loading}
      title={title}
      className={cn(
        'inline-flex items-center justify-center gap-2 font-medium rounded-lg border transition-all duration-200 ease-out active:scale-[0.97]',
        'disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-charlie-cyan/20',
        variantStyles[variant],
        sizeStyles[size],
        className,
      )}
    >
      {loading && <Loader2 size={12} className="animate-spin" />}
      {children}
    </button>
  )
}
