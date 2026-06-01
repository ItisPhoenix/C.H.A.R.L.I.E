'use client'

import { cn } from '@/lib/utils'

interface GlassCardProps {
  children: React.ReactNode
  className?: string
  glow?: boolean
  hover?: boolean
  onClick?: () => void
}

export function GlassCard({ children, className, glow, hover = false, onClick }: GlassCardProps) {
  return (
    <div
      className={cn(
        'glass-card flex flex-col overflow-hidden p-4 transition-all duration-300 ease-in-out',
        glow && 'glow-cyan',
        hover && 'hover:border-charlie-cyan/40 hover:shadow-neon-glow',
        onClick && 'cursor-pointer',
        className,
      )}
      onClick={onClick}
    >
      {children}
    </div>
  )
}
