'use client'

import { cn } from '@/lib/utils'

interface GlassCardProps {
  children: React.ReactNode
  className?: string
  glow?: boolean
  hover?: boolean
  hudCorners?: boolean
  onClick?: () => void
}

export function GlassCard({ children, className, glow, hover = true, hudCorners, onClick }: GlassCardProps) {
  return (
    <div
      className={cn(
        'glass-card p-4 transition-all duration-200',
        glow && 'glow-cyan',
        hover && 'hover:border-charlie-cyan/30 hover:shadow-neon-cyan-sm',
        hudCorners && 'relative',
        onClick && 'cursor-pointer',
        className,
      )}
      onClick={onClick}
    >
      {hudCorners && (
        <>
          <div className="absolute top-0 left-0 w-3 h-3 pointer-events-none z-10" style={{ borderTop: '2px solid rgba(0, 212, 255, 0.4)', borderLeft: '2px solid rgba(0, 212, 255, 0.4)' }} />
          <div className="absolute top-0 right-0 w-3 h-3 pointer-events-none z-10" style={{ borderTop: '2px solid rgba(0, 212, 255, 0.4)', borderRight: '2px solid rgba(0, 212, 255, 0.4)' }} />
          <div className="absolute bottom-0 left-0 w-3 h-3 pointer-events-none z-10" style={{ borderBottom: '2px solid rgba(0, 212, 255, 0.4)', borderLeft: '2px solid rgba(0, 212, 255, 0.4)' }} />
          <div className="absolute bottom-0 right-0 w-3 h-3 pointer-events-none z-10" style={{ borderBottom: '2px solid rgba(0, 212, 255, 0.4)', borderRight: '2px solid rgba(0, 212, 255, 0.4)' }} />
        </>
      )}
      {children}
    </div>
  )
}
