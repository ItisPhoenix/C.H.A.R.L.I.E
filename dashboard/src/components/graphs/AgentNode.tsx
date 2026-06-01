'use client'

import { Handle, Position } from '@xyflow/react'
import { StatusDot } from '@/components/ui/StatusDot'
import { cn } from '@/lib/utils'

interface AgentNodeData {
  label: string
  role: string
  status: 'running' | 'idle' | 'error'
  currentTask?: string
  toolsCount?: number
  skillsCount?: number
}

const statusMap = {
  running: 'online' as const,
  idle: 'idle' as const,
  error: 'error' as const,
}

export function AgentNode({ data }: { data: AgentNodeData }) {
  return (
    <div
      className={cn(
        'glass-card p-3 min-w-[180px] max-w-[220px]',
        data.status === 'running' && 'border-charlie-cyan/30 shadow-neon-cyan-sm',
      )}
    >
      <Handle type="target" position={Position.Top} className="!bg-charlie-cyan !w-2 !h-2" />
      <div className="flex items-center gap-2 mb-1">
        <StatusDot status={statusMap[data.status]} pulse={data.status === 'running'} />
        <span className="font-display text-sm text-charlie-text tracking-wide">{data.label}</span>
      </div>
      <div className="text-[10px] text-charlie-dim font-mono uppercase">{data.role}</div>
      {(data.toolsCount != null || data.skillsCount != null) && (
        <div className="text-[10px] text-charlie-dim/70 font-mono mt-0.5">
          {data.toolsCount != null && <span>{data.toolsCount} tools</span>}
          {data.toolsCount != null && data.skillsCount != null && data.skillsCount > 0 && <span> · </span>}
          {data.skillsCount != null && data.skillsCount > 0 && <span>{data.skillsCount} skills</span>}
        </div>
      )}
      {data.currentTask && (
        <div className="text-xs text-charlie-cyan mt-1 font-body line-clamp-1">{data.currentTask}</div>
      )}
      <Handle type="source" position={Position.Bottom} className="!bg-charlie-cyan !w-2 !h-2" />
    </div>
  )
}
