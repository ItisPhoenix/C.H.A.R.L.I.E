'use client'

import { Handle, Position } from '@xyflow/react'
import { cn } from '@/lib/utils'

interface MemoryNodeData {
  label: string
  type: 'fact' | 'preference' | 'entity' | 'episode'
  source: string
  timestamp?: string
}

const typeColors: Record<string, { bg: string; border: string; text: string }> = {
  fact: { bg: 'bg-charlie-cyan/10', border: 'border-charlie-cyan/30', text: 'text-charlie-cyan' },
  preference: { bg: 'bg-charlie-purple/10', border: 'border-charlie-purple/30', text: 'text-charlie-purple' },
  entity: { bg: 'bg-charlie-green/10', border: 'border-charlie-green/30', text: 'text-charlie-green' },
  episode: { bg: 'bg-charlie-amber/10', border: 'border-charlie-amber/30', text: 'text-charlie-amber' },
}

export function MemoryNode({ data }: { data: MemoryNodeData }) {
  const colors = typeColors[data.type] || typeColors.fact

  return (
    <div className={cn('glass-card p-3 min-w-[180px] max-w-[220px]', colors.bg, colors.border)}>
      <Handle type="target" position={Position.Top} className="!bg-charlie-cyan !w-2 !h-2" />
      <div className={cn('font-display text-xs tracking-wide uppercase mb-1', colors.text)}>
        {data.type}
      </div>
      <div className="text-sm text-charlie-text font-body line-clamp-2">{data.label}</div>
      <div className="text-[10px] text-charlie-dim mt-1 font-mono">{data.source}</div>
      <Handle type="source" position={Position.Bottom} className="!bg-charlie-cyan !w-2 !h-2" />
    </div>
  )
}
