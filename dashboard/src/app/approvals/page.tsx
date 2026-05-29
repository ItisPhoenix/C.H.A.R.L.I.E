'use client'

import { useEffect, useState, useCallback } from 'react'
import { GlassCard } from '@/components/ui/GlassCard'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { ProgressBar } from '@/components/ui/ProgressBar'
import { Modal } from '@/components/ui/Modal'
import { EmptyState } from '@/components/ui/EmptyState'
import { LoadingSpinner } from '@/components/ui/LoadingSpinner'
import { ErrorState } from '@/components/ui/ErrorState'
import { HudCorners } from '@/components/background/HudCorners'
import { PageHeader } from '@/components/layout/PageHeader'
import { fetchApprovals, approveAction, denyAction } from '@/lib/api'
import { riskTierLabel, cn } from '@/lib/utils'
import type { Approval } from '@/lib/types'

// Kanban columns: Pending, Approved, Denied
// For now we only have pending from the API; approved/denied are cleared on action
// In a full implementation these would come from the API as well

export default function ApprovalsPage() {
  const [approvals, setApprovals] = useState<Approval[]>([])
  const [recentlyApproved, setRecentlyApproved] = useState<Approval[]>([])
  const [recentlyDenied, setRecentlyDenied] = useState<Approval[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [actionLoading, setActionLoading] = useState<string | null>(null)
  const [confirmTier3, setConfirmTier3] = useState<Approval | null>(null)

  const loadApprovals = useCallback(async () => {
    try {
      const data = await fetchApprovals()
      setApprovals(data.pending || [])
      setError(null)
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to fetch approvals')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    loadApprovals()
    const interval = setInterval(loadApprovals, 3000)
    return () => clearInterval(interval)
  }, [loadApprovals])

  function handleApproveClick(approval: Approval) {
    if (approval.risk_tier === 3) {
      setConfirmTier3(approval)
    } else {
      doApprove(approval)
    }
  }

  async function doApprove(approval: Approval) {
    setActionLoading(approval.id)
    try {
      await approveAction(approval.id)
      setRecentlyApproved((prev) => [approval, ...prev])
      await loadApprovals()
    } catch {} finally {
      setActionLoading(null)
      setConfirmTier3(null)
    }
  }

  async function handleDeny(approval: Approval) {
    setActionLoading(approval.id)
    try {
      await denyAction(approval.id)
      setRecentlyDenied((prev) => [approval, ...prev])
      await loadApprovals()
    } catch {} finally {
      setActionLoading(null)
    }
  }

  if (loading) {
    return (
      <div className="max-w-6xl mx-auto space-y-4">
        <PageHeader title="Approvals" subtitle="Loading..." />
        <div className="flex items-center justify-center h-64">
          <LoadingSpinner size="lg" label="Fetching pending approvals..." />
        </div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="max-w-6xl mx-auto space-y-4">
        <PageHeader title="Approvals" />
        <GlassCard>
          <ErrorState error={error} onRetry={loadApprovals} />
        </GlassCard>
      </div>
    )
  }

  return (
    <div className="max-w-6xl mx-auto space-y-6">
      <PageHeader
        title="Approvals"
        subtitle={`${approvals.length} pending`}
      />

      {/* Kanban columns */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Pending Column */}
        <KanbanColumn
          title="Pending"
          color="amber"
          count={approvals.length}
          items={approvals}
          emptyText="No pending approvals"
          renderItem={(approval) => (
            <ApprovalCard
              key={approval.id}
              approval={approval}
              actionLoading={actionLoading}
              onApprove={handleApproveClick}
              onDeny={handleDeny}
            />
          )}
        />

        {/* Approved Column */}
        <KanbanColumn
          title="Approved"
          color="green"
          count={recentlyApproved.length}
          items={recentlyApproved}
          emptyText="No recently approved"
          renderItem={(approval) => (
            <ApprovalResultCard key={approval.id} approval={approval} status="approved" />
          )}
        />

        {/* Denied Column */}
        <KanbanColumn
          title="Denied"
          color="red"
          count={recentlyDenied.length}
          items={recentlyDenied}
          emptyText="No recently denied"
          renderItem={(approval) => (
            <ApprovalResultCard key={approval.id} approval={approval} status="denied" />
          )}
        />
      </div>

      {/* Tier 3 Confirmation Modal */}
      <Modal
        open={confirmTier3 !== null}
        onClose={() => setConfirmTier3(null)}
        title="Destructive Action Confirmation"
      >
        {confirmTier3 && (
          <>
            <div className="space-y-3 mb-4">
              <div className="flex items-center gap-2">
                <Badge variant="red">TIER 3 - DESTRUCTIVE</Badge>
              </div>
              <p className="text-charlie-text text-sm font-body">{confirmTier3.description}</p>
              <p className="text-charlie-dim text-xs font-mono">
                {confirmTier3.action}(
                {JSON.stringify(confirmTier3.args).length > 80
                  ? JSON.stringify(confirmTier3.args).slice(0, 80) + '...'
                  : JSON.stringify(confirmTier3.args)})
              </p>
              <p className="text-charlie-red text-xs font-body">
                This action cannot be undone. Proceed with caution.
              </p>
            </div>
            <div className="flex justify-end gap-2">
              <Button variant="ghost" size="sm" onClick={() => setConfirmTier3(null)}>Cancel</Button>
              <Button
                variant="danger"
                size="sm"
                loading={actionLoading === confirmTier3.id}
                onClick={() => doApprove(confirmTier3)}
              >
                Confirm Approve
              </Button>
            </div>
          </>
        )}
      </Modal>
    </div>
  )
}

// --- Kanban Column ---

function KanbanColumn<T>({
  title,
  color,
  count,
  items,
  emptyText,
  renderItem,
}: {
  title: string
  color: 'cyan' | 'green' | 'amber' | 'red' | 'dim' | 'orange' | 'purple'
  count: number
  items: T[]
  emptyText: string
  renderItem: (item: T) => React.ReactNode
}) {
  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between pb-2 border-b border-charlie-border">
        <h3 className="font-display text-sm tracking-wide text-charlie-cyan uppercase">{title}</h3>
        <Badge variant={color}>{count}</Badge>
      </div>
      {items.length === 0 ? (
        <GlassCard className="!p-4">
          <p className="text-charlie-dim text-sm text-center font-body">{emptyText}</p>
        </GlassCard>
      ) : (
        <div className="space-y-3">
          {items.map((item) => renderItem(item))}
        </div>
      )}
    </div>
  )
}

// --- ApprovalCard (for pending) ---

interface ApprovalCardProps {
  approval: Approval
  actionLoading: string | null
  onApprove: (a: Approval) => void
  onDeny: (a: Approval) => void
}

function ApprovalCard({ approval, actionLoading, onApprove, onDeny }: ApprovalCardProps) {
  const tier = riskTierLabel(approval.risk_tier)
  const isBusy = actionLoading === approval.id
  const argPreview = JSON.stringify(approval.args)
  const truncatedArgs = argPreview.length > 60 ? argPreview.slice(0, 60) + '...' : argPreview

  const tierBadgeVariant = (() => {
    switch (tier.color) {
      case 'cyan': return 'cyan' as const
      case 'amber': return 'amber' as const
      case 'orange': return 'orange' as const
      case 'red': return 'red' as const
      default: return 'dim' as const
    }
  })()

  const progressColor = (() => {
    if (approval.risk_tier >= 3) return 'red' as const
    if (approval.risk_tier >= 2) return 'amber' as const
    return 'cyan' as const
  })()

  return (
    <HudCorners>
      <GlassCard className="hover:shadow-neon-cyan-sm transition-all">
        <div className="flex items-start justify-between gap-4 mb-2">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 mb-1">
              <Badge variant={tierBadgeVariant}>
                TIER {approval.risk_tier} - {tier.label}
              </Badge>
            </div>
            <p className="text-sm text-charlie-text mb-1 font-body">{approval.description}</p>
            <p className="text-xs text-charlie-dim font-mono truncate">
              {approval.action}({truncatedArgs})
            </p>
          </div>
        </div>

        {/* Countdown bar */}
        <div className="mb-3">
          <ProgressBar value={approval.remaining} max={60} color={progressColor} />
          <p className="text-xs text-charlie-dim mt-1 font-mono">{approval.remaining}s remaining</p>
        </div>

        {/* Action buttons */}
        <div className="flex gap-2 pt-2 border-t border-charlie-border">
          <Button
            variant="danger"
            size="sm"
            loading={isBusy}
            onClick={() => onDeny(approval)}
            className="flex-1"
          >
            Deny
          </Button>
          <Button
            variant="primary"
            size="sm"
            loading={isBusy}
            onClick={() => onApprove(approval)}
            className="flex-1"
          >
            Approve
          </Button>
        </div>
      </GlassCard>
    </HudCorners>
  )
}

// --- ApprovalResultCard (for approved/denied) ---

function ApprovalResultCard({ approval, status }: { approval: Approval; status: 'approved' | 'denied' }) {
  const tier = riskTierLabel(approval.risk_tier)
  return (
    <GlassCard className={cn(
      'opacity-70',
      status === 'approved' ? 'border-charlie-green/20' : 'border-charlie-red/20',
    )}>
      <div className="flex items-center gap-2 mb-1">
        <Badge variant={status === 'approved' ? 'green' : 'red'}>{status.toUpperCase()}</Badge>
        <Badge variant="dim">TIER {approval.risk_tier}</Badge>
      </div>
      <p className="text-sm text-charlie-dim font-body">{approval.description}</p>
    </GlassCard>
  )
}
