'use client'

import { type ReactNode } from 'react'

interface HudCornersProps {
  children: ReactNode
  className?: string
}

export function HudCorners({ children, className = '' }: HudCornersProps) {
  const borderColor = 'var(--charlie-cyan)'
  const borderStyle = '2px solid'
  const opacity = '0.4'

  return (
    <div className={`relative ${className}`}>
      {children}
      {/* Top-left */}
      <div
        className="absolute top-0 left-0 w-3 h-3 pointer-events-none z-10"
        style={{
          borderTop: `${borderStyle} ${borderColor}`,
          borderLeft: `${borderStyle} ${borderColor}`,
          opacity,
        }}
      />
      {/* Top-right */}
      <div
        className="absolute top-0 right-0 w-3 h-3 pointer-events-none z-10"
        style={{
          borderTop: `${borderStyle} ${borderColor}`,
          borderRight: `${borderStyle} ${borderColor}`,
          opacity,
        }}
      />
      {/* Bottom-left */}
      <div
        className="absolute bottom-0 left-0 w-3 h-3 pointer-events-none z-10"
        style={{
          borderBottom: `${borderStyle} ${borderColor}`,
          borderLeft: `${borderStyle} ${borderColor}`,
          opacity,
        }}
      />
      {/* Bottom-right */}
      <div
        className="absolute bottom-0 right-0 w-3 h-3 pointer-events-none z-10"
        style={{
          borderBottom: `${borderStyle} ${borderColor}`,
          borderRight: `${borderStyle} ${borderColor}`,
          opacity,
        }}
      />
    </div>
  )
}
