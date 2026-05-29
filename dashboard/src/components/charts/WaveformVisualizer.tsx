'use client'

import { cn } from '@/lib/utils'

interface WaveformVisualizerProps {
  active: boolean
  volumeLevel?: number
  className?: string
}

export function WaveformVisualizer({ active, volumeLevel = 0, className }: WaveformVisualizerProps) {
  const bars = 12
  return (
    <div className={cn('flex items-end justify-center gap-1 h-12', className)}>
      {Array.from({ length: bars }).map((_, i) => {
        const height = active ? 4 + Math.sin((Date.now() / 200 + i * 0.8)) * (volumeLevel * 20 + 8) : 4
        return (
          <div
            key={i}
            className="w-1 bg-charlie-cyan rounded-full transition-all"
            style={{
              height: `${Math.max(4, height)}px`,
              opacity: active ? 0.8 : 0.2,
              animation: active ? `waveform 0.${5 + i}s ease-in-out infinite` : 'none',
              ['--wave-height' as string]: `${20 + volumeLevel * 30}px`,
            }}
          />
        )
      })}
    </div>
  )
}
