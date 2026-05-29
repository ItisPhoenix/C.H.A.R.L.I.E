'use client'

import { Handle, Position } from '@xyflow/react'
import { cn } from '@/lib/utils'

interface FlowNodeData {
  label: string
  type: 'trigger' | 'condition' | 'action'
  description?: string
  active?: boolean
}

const typeConfig: Record<string, { bg: string; border: string; icon: string }> = {
  trigger: { bg: 'bg-charlie-amber/10', border: 'border-charlie-amber/30', icon: '⚡' },
  condition: { bg: 'bg-charlie-cyan/10', border: 'border-charlie-cyan/30', icon: '◇' },
  action: { bg: 'bg-charlie-green/10', border: 'border-charlie-green/30', icon: '▶' },
}

export function FlowNode({ data }: { data: FlowNodeData }) {
  const config = typeConfig[data.type] || typeConfig.action

  return (
    <div
      className={cn(
        'glass-card p-3 min-w-[160px] max-w-[200px]',
        config.bg,
        config.border,
        data.active && 'shadow-neon-cyan-sm',
      )}
    >
      <Handle type="target" position={Position.Left} className="!bg-charlie-cyan !w-2 !h-2" />
      <div className="flex items-center gap-2 mb-1">
        <span className="text-sm">{config.icon}</span>
        <span className="font-display text-xs tracking-wide uppercase text-charlie-dim">{data.type}</span>
      </div>
      <div className="text-sm text-charlie-text font-body">{data.label}</div>
      {data.description && (
        <div className="text-[10px] text-charlie-dim mt-1 font-body line-clamp-2">{data.description}</div>
      )}
      <Handle type="source" position={Position.Right} className="!bg-charlie-cyan !w-2 !h-2" />
    </div>
  )
}
