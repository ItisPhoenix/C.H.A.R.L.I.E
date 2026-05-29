'use client'

import { useEffect, useState } from 'react'

export function SweepScanLine() {
  const [visible, setVisible] = useState(false)

  useEffect(() => {
    const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    if (prefersReducedMotion) return

    const interval = setInterval(() => {
      setVisible(true)
      setTimeout(() => setVisible(false), 2000)
    }, 8000)

    // Trigger first one after a short delay
    const initial = setTimeout(() => {
      setVisible(true)
      setTimeout(() => setVisible(false), 2000)
    }, 2000)

    return () => {
      clearInterval(interval)
      clearTimeout(initial)
    }
  }, [])

  if (!visible) return null

  const isLight = typeof document !== 'undefined' && document.documentElement.classList.contains('light')
  const baseColor = isLight ? '100, 116, 139' : '0, 212, 255'
  const intensity = isLight ? 0.3 : 0.6

  return (
    <div
      className="fixed left-0 right-0 pointer-events-none z-[1]"
      style={{
        height: '2px',
        background: `linear-gradient(90deg, transparent, rgba(${baseColor}, ${intensity}), transparent)`,
        boxShadow: `0 0 20px rgba(${baseColor}, ${intensity * 0.5}), 0 0 60px rgba(${baseColor}, ${intensity * 0.15})`,
        animation: 'scanline 2s ease-out forwards',
        top: 0,
      }}
    />
  )
}
