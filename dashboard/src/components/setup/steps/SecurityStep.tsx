'use client'

import { Shield, Lock, Eye } from 'lucide-react'
import { type SetupData } from '../types'

const TIERS = [
  { value: 0, label: 'TIER 0', desc: 'Read-only operations. Auto-approved.', color: 'text-charlie-green' },
  { value: 1, label: 'TIER 1', desc: 'Low-risk actions. Auto-approved with high confidence.', color: 'text-charlie-cyan' },
  { value: 2, label: 'TIER 2', desc: 'Medium-risk. Always asks for approval.', color: 'text-charlie-amber' },
  { value: 3, label: 'TIER 3', desc: 'Destructive. Always asks + confirmation modal.', color: 'text-charlie-red' },
]

export function SecurityStep({ data, onChange }: { data: SetupData; onChange: (d: SetupData) => void }) {
  return (
    <div className="space-y-8">
      <div className="text-center">
        <h2 className="font-display text-2xl text-charlie-cyan tracking-wide mb-2">Security</h2>
        <p className="text-charlie-dim text-sm font-body">Configure risk thresholds and guardian settings</p>
      </div>

      <div className="space-y-4">
        {/* Risk tier slider */}
        <div>
          <label className="text-charlie-dim text-xs font-display tracking-wider uppercase mb-3 flex items-center gap-2">
            <Shield size={12} /> Maximum Auto-Approved Risk Tier
          </label>
          <div className="grid grid-cols-4 gap-2">
            {TIERS.map((tier) => (
              <button
                key={tier.value}
                onClick={() => onChange({ ...data, risk_tier: tier.value })}
                className={`p-3 rounded-lg border text-center transition-all cursor-pointer ${
                  data.risk_tier === tier.value
                    ? 'border-charlie-cyan/50 bg-charlie-cyan/10'
                    : 'border-charlie-border hover:border-charlie-cyan/20'
                }`}
              >
                <div className={`font-display text-xs tracking-wider ${tier.color}`}>{tier.label}</div>
                <div className="text-charlie-dim text-[10px] mt-1">{tier.desc}</div>
              </button>
            ))}
          </div>
        </div>

        {/* Guardian toggle */}
        <div className="flex items-center justify-between glass-card p-4 rounded-lg">
          <div className="flex items-center gap-3">
            <Lock size={20} className="text-charlie-cyan" />
            <div>
              <div className="font-body text-sm text-charlie-text">Guardian Enabled</div>
              <div className="text-charlie-dim text-xs">Rate limits, path security, AST scanning</div>
            </div>
          </div>
          <button
            onClick={() => onChange({ ...data, guardian_enabled: !data.guardian_enabled })}
            className={`w-12 h-6 rounded-full transition-all cursor-pointer ${
              data.guardian_enabled ? 'bg-charlie-cyan' : 'bg-charlie-border'
            }`}
          >
            <div
              className={`w-5 h-5 rounded-full bg-white transition-transform ${
                data.guardian_enabled ? 'translate-x-6' : 'translate-x-0.5'
              }`}
            />
          </button>
        </div>

        {/* Auto-approve threshold */}
        <div>
          <label className="text-charlie-dim text-xs font-display tracking-wider uppercase mb-2 flex items-center gap-2">
            <Eye size={12} /> Auto-Approve Confidence Threshold
          </label>
          <div className="flex items-center gap-4">
            <input
              type="range"
              min="0.5"
              max="0.99"
              step="0.05"
              value={data.auto_approve_threshold}
              onChange={(e) => onChange({ ...data, auto_approve_threshold: parseFloat(e.target.value) })}
              className="flex-1 accent-charlie-cyan"
            />
            <span className="font-mono text-sm text-charlie-cyan w-12 text-right">
              {Math.round(data.auto_approve_threshold * 100)}%
            </span>
          </div>
        </div>
      </div>
    </div>
  )
}
