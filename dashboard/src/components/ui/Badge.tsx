'use client'

import { cn } from '@/lib/utils'

type BadgeVariant = 'cyan' | 'green' | 'amber' | 'red' | 'dim' | 'orange' | 'purple'

const variantStyles: Record<BadgeVariant, string> = {
  cyan: 'bg-charlie-cyan/10 text-charlie-cyan border-charlie-cyan/20',
  green: 'bg-charlie-green/10 text-charlie-green border-charlie-green/20',
  amber: 'bg-charlie-amber/10 text-charlie-amber border-charlie-amber/20',
  orange: 'bg-charlie-orange/10 text-charlie-orange border-charlie-orange/20',
  red: 'bg-charlie-red/10 text-charlie-red border-charlie-red/20',
  dim: 'bg-charlie-card text-charlie-dim border-charlie-border',
  purple: 'bg-charlie-purple/10 text-charlie-purple border-charlie-purple/20',
}

interface BadgeProps {
  variant?: BadgeVariant
  children: React.ReactNode
  className?: string
}

export function Badge({ variant = 'cyan', children, className }: BadgeProps) {
  return (
    <span
      className={cn(
        'inline-flex items-center px-2 py-0.5 text-[11px] font-medium rounded-md border font-sans',
        variantStyles[variant],
        className,
      )}
    >
      {children}
    </span>
  )
}
