'use client'

import { cn } from '@/lib/utils'
import { useEffect, useRef, useState } from 'react'

interface MetricProps {
  label: string
  value: string
  trend?: 'up' | 'down' | 'neutral'
  animate?: boolean
  className?: string
}

export function Metric({ label, value, trend, animate = true, className }: MetricProps) {
  const [displayValue, setDisplayValue] = useState(value)
  const prevValue = useRef(value)

  useEffect(() => {
    if (!animate) {
      setDisplayValue(value)
      return
    }

    // Try to parse numeric values for count-up animation
    const numMatch = value.match(/^([\d,.]+)\s*(.*)$/)
    const prevMatch = prevValue.current.match(/^([\d,.]+)\s*(.*)$/)

    if (numMatch && prevMatch) {
      const target = parseFloat(numMatch[1].replace(/,/g, ''))
      const start = parseFloat(prevMatch[1].replace(/,/g, ''))
      const suffix = numMatch[2]

      if (!isNaN(target) && !isNaN(start) && target !== start) {
        const duration = 600
        const startTime = performance.now()

        const tick = (now: number) => {
          const elapsed = now - startTime
          const progress = Math.min(elapsed / duration, 1)
          const eased = 1 - Math.pow(1 - progress, 3) // ease-out cubic
          const current = start + (target - start) * eased

          const formatted = target >= 100
            ? Math.round(current).toLocaleString()
            : current.toFixed(1)

          setDisplayValue(`${formatted}${suffix}`)

          if (progress < 1) {
            requestAnimationFrame(tick)
          }
        }

        requestAnimationFrame(tick)
        prevValue.current = value
        return
      }
    }

    setDisplayValue(value)
    prevValue.current = value
  }, [value, animate])

  return (
    <div className={cn('text-center', className)}>
      <div className="font-mono text-charlie-text font-medium text-xl tracking-tight">
        {displayValue}
      </div>
      <div className="font-sans text-charlie-dim text-xs flex items-center justify-center gap-1.5 mt-0.5">
        {trend === 'up' && <span className="text-charlie-green">↑</span>}
        {trend === 'down' && <span className="text-charlie-red">↓</span>}
        {label}
      </div>
    </div>
  )
}
