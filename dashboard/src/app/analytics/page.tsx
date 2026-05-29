'use client'

import { useState, useEffect, useCallback } from 'react'
import { PageHeader } from '@/components/layout/PageHeader'
import { GlassCard } from '@/components/ui/GlassCard'
import { HudCorners } from '@/components/background/HudCorners'
import { LoadingSpinner } from '@/components/ui/LoadingSpinner'
import { fetchToolLog } from '@/lib/api'
import {
  AreaChart,
  Area,
  LineChart,
  Line,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from 'recharts'

// Empty state data — all zeros, will be populated from API
const emptyTimeSlots = [
  { time: '00:00', calls: 0 }, { time: '04:00', calls: 0 },
  { time: '08:00', calls: 0 }, { time: '12:00', calls: 0 },
  { time: '16:00', calls: 0 }, { time: '20:00', calls: 0 },
]

interface ToolExecution {
  tool_name?: string
  event_type?: string
  outcome_type?: string
  duration_ms?: number
  timestamp?: number
}

const CustomTooltip = ({ active, payload, label }: { active?: boolean; payload?: Array<{ name: string; value: number; color: string }>; label?: string }) => {
  if (!active || !payload) return null
  return (
    <div className="glass-tooltip">
      <p className="text-charlie-dim text-xs mb-1">{label}</p>
      {payload.map((p, i) => (
        <p key={i} style={{ color: p.color }} className="text-sm font-mono">
          {p.name}: {p.value}
        </p>
      ))}
    </div>
  )
}

export default function AnalyticsPage() {
  const [toolUsage, setToolUsage] = useState(emptyTimeSlots)
  const [responseTime, setResponseTime] = useState<Array<{ time: string; avg: number; p95: number }>>([])
  const [toolActivity, setToolActivity] = useState<Array<{ name: string; tasks: number }>>([])
  const [loading, setLoading] = useState(true)

  const fetchData = useCallback(async () => {
    try {
      const toolsData = await fetchToolLog()
      const executions: ToolExecution[] = toolsData.executions || []

      if (executions.length > 0) {
        // Group by hour
        const hourCounts: Record<string, number> = {}
        executions.forEach((ex) => {
          const hour = ex.timestamp ? new Date(ex.timestamp * 1000).getHours() : 0
          const key = `${String(hour).padStart(2, '0')}:00`
          hourCounts[key] = (hourCounts[key] || 0) + 1
        })
        setToolUsage(['00:00', '04:00', '08:00', '12:00', '16:00', '20:00'].map((t) => ({
          time: t,
          calls: hourCounts[t] || 0,
        })))

        // Response times — real data, no random jitter
        const durations = executions.filter((e) => e.duration_ms).map((e) => e.duration_ms!)
        if (durations.length > 0) {
          const avg = Math.round(durations.reduce((a, b) => a + b, 0) / durations.length)
          const sorted = [...durations].sort((a, b) => a - b)
          const p95 = sorted[Math.floor(sorted.length * 0.95)] || avg
          setResponseTime(['00:00', '04:00', '08:00', '12:00', '16:00', '20:00'].map((t) => ({
            time: t,
            avg,
            p95,
          })))
        }

        // Top tools by usage
        const toolCounts: Record<string, number> = {}
        executions.forEach((ex) => {
          const name = ex.tool_name || 'unknown'
          toolCounts[name] = (toolCounts[name] || 0) + 1
        })
        const topTools = Object.entries(toolCounts)
          .sort(([, a], [, b]) => b - a)
          .slice(0, 5)
          .map(([name, count]) => ({ name, tasks: count }))
        if (topTools.length > 0) setToolActivity(topTools)
      }
    } catch {} finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchData()
  }, [fetchData])

  if (loading) {
    return (
      <div className="flex items-center justify-center h-[60vh]">
        <LoadingSpinner label="Loading analytics..." />
      </div>
    )
  }

  return (
    <div>
      <PageHeader title="Analytics" subtitle="Usage metrics, response times, and system activity" />

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Tool Usage */}
        <HudCorners>
          <GlassCard className="p-5">
            <h3 className="font-display text-sm text-charlie-cyan mb-4 tracking-wide">Tool Usage</h3>
            <ResponsiveContainer width="100%" height={250}>
              <AreaChart data={toolUsage}>
                <defs>
                  <linearGradient id="cyanGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#00D4FF" stopOpacity={0.3} />
                    <stop offset="100%" stopColor="#00D4FF" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(0,212,255,0.1)" />
                <XAxis dataKey="time" stroke="#64748B" fontSize={12} />
                <YAxis stroke="#64748B" fontSize={12} />
                <Tooltip content={<CustomTooltip />} />
                <Area
                  type="monotone"
                  dataKey="calls"
                  stroke="#00D4FF"
                  strokeWidth={2}
                  fill="url(#cyanGrad)"
                  animationDuration={1500}
                />
              </AreaChart>
            </ResponsiveContainer>
          </GlassCard>
        </HudCorners>

        {/* Response Time */}
        <HudCorners>
          <GlassCard className="p-5">
            <h3 className="font-display text-sm text-charlie-cyan mb-4 tracking-wide">Response Time (ms)</h3>
            <ResponsiveContainer width="100%" height={250}>
              <LineChart data={responseTime}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(0,212,255,0.1)" />
                <XAxis dataKey="time" stroke="#64748B" fontSize={12} />
                <YAxis stroke="#64748B" fontSize={12} />
                <Tooltip content={<CustomTooltip />} />
                <Line type="monotone" dataKey="avg" stroke="#00D4FF" strokeWidth={2} dot={false} animationDuration={1500} />
                <Line type="monotone" dataKey="p95" stroke="#F59E0B" strokeWidth={1.5} strokeDasharray="5 5" dot={false} animationDuration={1500} />
              </LineChart>
            </ResponsiveContainer>
          </GlassCard>
        </HudCorners>

        {/* Tool Usage Breakdown */}
        <HudCorners>
          <GlassCard className="p-5">
            <h3 className="font-display text-sm text-charlie-cyan mb-4 tracking-wide">Tool Usage Breakdown</h3>
            <ResponsiveContainer width="100%" height={250}>
              <BarChart data={toolActivity}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(0,212,255,0.1)" />
                <XAxis dataKey="name" stroke="#64748B" fontSize={11} />
                <YAxis stroke="#64748B" fontSize={12} />
                <Tooltip content={<CustomTooltip />} />
                <Bar dataKey="tasks" fill="#A855F7" radius={[4, 4, 0, 0]} animationDuration={1500} />
              </BarChart>
            </ResponsiveContainer>
          </GlassCard>
        </HudCorners>

        {/* Top Tools */}
        <HudCorners>
          <GlassCard className="p-5">
            <h3 className="font-display text-sm text-charlie-cyan mb-4 tracking-wide">Top Tools by Usage</h3>
            <ResponsiveContainer width="100%" height={250}>
              <BarChart data={toolActivity} layout="vertical">
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(0,212,255,0.1)" />
                <XAxis type="number" stroke="#64748B" fontSize={12} />
                <YAxis type="category" dataKey="name" stroke="#64748B" fontSize={11} width={100} />
                <Tooltip content={<CustomTooltip />} />
                <Bar dataKey="tasks" fill="#00D4FF" radius={[0, 4, 4, 0]} animationDuration={1500} />
              </BarChart>
            </ResponsiveContainer>
          </GlassCard>
        </HudCorners>
      </div>
    </div>
  )
}
