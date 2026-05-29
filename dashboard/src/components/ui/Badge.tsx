'use client'

import { cn } from '@/lib/utils'

type BadgeVariant = 'cyan' | 'green' | 'amber' | 'red' | 'dim' | 'orange' | 'purple'

const variantStyles: Record<BadgeVariant, string> = {
  cyan: 'bg-charlie-cyan/20 text-charlie-cyan border-charlie-cyan/30 shadow-[0_0_8px_rgba(0,212,255,0.15)]',
  green: 'bg-charlie-green/20 text-charlie-green border-charlie-green/30 shadow-[0_0_8px_rgba(34,197,94,0.15)]',
  amber: 'bg-charlie-amber/20 text-charlie-amber border-charlie-amber/30 shadow-[0_0_8px_rgba(245,158,11,0.15)]',
  orange: 'bg-charlie-orange/20 text-charlie-orange border-charlie-orange/30 shadow-[0_0_8px_rgba(249,115,22,0.15)]',
  red: 'bg-charlie-red/20 text-charlie-red border-charlie-red/30 shadow-[0_0_8px_rgba(239,68,68,0.15)]',
  dim: 'bg-charlie-dim/20 text-charlie-dim border-charlie-dim/30',
  purple: 'bg-charlie-purple/20 text-charlie-purple border-charlie-purple/30 shadow-[0_0_8px_rgba(168,85,247,0.15)]',
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
        'inline-flex items-center px-2 py-0.5 text-xs font-medium rounded-full border font-display tracking-wider',
        variantStyles[variant],
        className,
      )}
    >
      {children}
    </span>
  )
}
