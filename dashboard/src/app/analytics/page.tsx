'use client'

import { useState, useEffect, useCallback } from 'react'
import { PageHeader } from '@/components/layout/PageHeader'
import { GlassCard } from '@/components/ui/GlassCard'
import { LoadingSpinner } from '@/components/ui/LoadingSpinner'
import { ErrorState } from '@/components/ui/ErrorState'
import { EmptyState } from '@/components/ui/EmptyState'
import { fetchToolLog } from '@/lib/api'
import { useChartColors } from '@/lib/utils'
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
  const [error, setError] = useState<string | null>(null)
  const colors = useChartColors()

  const fetchData = useCallback(async () => {
    try {
      setError(null)
      const toolsData = await fetchToolLog()
      const executions: ToolExecution[] = toolsData.executions || []

      if (executions.length > 0) {
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
    } catch (e) {
      console.error('Failed to load analytics:', e)
      setError('Failed to load analytics data')
    } finally {
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

  if (error) {
    return <ErrorState error={error} onRetry={fetchData} />
  }

  return (
    <div className="max-w-6xl mx-auto space-y-6">
      <PageHeader title="Analytics" subtitle="Usage metrics, response times, and system activity" />

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Tool Usage */}
        <GlassCard className="p-5">
          <h3 className="font-display text-sm text-charlie-cyan mb-4 tracking-[0.1em] uppercase">Tool Usage</h3>
          {toolUsage.every((t) => t.calls === 0) ? (
            <EmptyState terminal title="No tool usage data" description="Tool usage will appear here once tools are executed" />
          ) : (
            <ResponsiveContainer width="100%" height={250}>
              <AreaChart data={toolUsage}>
                <defs>
                  <linearGradient id="cyanGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor={colors.cyan} stopOpacity={0.3} />
                    <stop offset="100%" stopColor={colors.cyan} stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke={colors.grid} />
                <XAxis dataKey="time" stroke={colors.dim} fontSize={12} />
                <YAxis stroke={colors.dim} fontSize={12} />
                <Tooltip content={<CustomTooltip />} />
                <Area type="monotone" dataKey="calls" stroke={colors.cyan} strokeWidth={2} fill="url(#cyanGrad)" animationDuration={1500} />
              </AreaChart>
            </ResponsiveContainer>
          )}
        </GlassCard>

        {/* Response Time */}
        <GlassCard className="p-5">
          <h3 className="font-display text-sm text-charlie-cyan mb-4 tracking-[0.1em] uppercase">Response Time (ms)</h3>
          {responseTime.length === 0 ? (
            <EmptyState terminal title="No response time data" description="Response times will appear here once tools are executed" />
          ) : (
            <ResponsiveContainer width="100%" height={250}>
              <LineChart data={responseTime}>
                <CartesianGrid strokeDasharray="3 3" stroke={colors.grid} />
                <XAxis dataKey="time" stroke={colors.dim} fontSize={12} />
                <YAxis stroke={colors.dim} fontSize={12} />
                <Tooltip content={<CustomTooltip />} />
                <Line type="monotone" dataKey="avg" stroke={colors.cyan} strokeWidth={2} dot={false} animationDuration={1500} />
                <Line type="monotone" dataKey="p95" stroke={colors.amber} strokeWidth={1.5} strokeDasharray="5 5" dot={false} animationDuration={1500} />
              </LineChart>
            </ResponsiveContainer>
          )}
        </GlassCard>

        {/* Tool Usage Breakdown */}
        <GlassCard className="p-5">
          <h3 className="font-display text-sm text-charlie-cyan mb-4 tracking-[0.1em] uppercase">Tool Usage Breakdown</h3>
          {toolActivity.length === 0 ? (
            <EmptyState terminal title="No tool activity" description="Tool breakdown will appear here once tools are used" />
          ) : (
            <ResponsiveContainer width="100%" height={250}>
              <BarChart data={toolActivity}>
                <CartesianGrid strokeDasharray="3 3" stroke={colors.grid} />
                <XAxis dataKey="name" stroke={colors.dim} fontSize={11} />
                <YAxis stroke={colors.dim} fontSize={12} />
                <Tooltip content={<CustomTooltip />} />
                <Bar dataKey="tasks" fill={colors.purple} radius={[4, 4, 0, 0]} animationDuration={1500} />
              </BarChart>
            </ResponsiveContainer>
          )}
        </GlassCard>

        {/* Top Tools */}
        <GlassCard className="p-5">
          <h3 className="font-display text-sm text-charlie-cyan mb-4 tracking-[0.1em] uppercase">Top Tools by Usage</h3>
          {toolActivity.length === 0 ? (
            <EmptyState terminal title="No tools used" description="Top tools will appear here once tools are used" />
          ) : (
            <ResponsiveContainer width="100%" height={250}>
              <BarChart data={toolActivity} layout="vertical">
                <CartesianGrid strokeDasharray="3 3" stroke={colors.grid} />
                <XAxis type="number" stroke={colors.dim} fontSize={12} />
                <YAxis type="category" dataKey="name" stroke={colors.dim} fontSize={11} width={100} />
                <Tooltip content={<CustomTooltip />} />
                <Bar dataKey="tasks" fill={colors.cyan} radius={[0, 4, 4, 0]} animationDuration={1500} />
              </BarChart>
            </ResponsiveContainer>
          )}
        </GlassCard>
      </div>
    </div>
  )
}
