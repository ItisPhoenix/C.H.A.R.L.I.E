'use client'

import { motion } from 'framer-motion'
import { Check, Rocket } from 'lucide-react'
import { type SetupData } from '../types'

export function CompleteStep({ data }: { data: SetupData }) {
  const connectedIntegrations = Object.entries(data.integrations)
    .filter(([, v]) => v)
    .map(([k]) => k)

  return (
    <div className="text-center space-y-8">
      {/* Success orb */}
      <motion.div
        className="mx-auto w-24 h-24 rounded-full flex items-center justify-center"
        style={{
          background: 'radial-gradient(circle, rgba(34,197,94,0.2) 0%, transparent 70%)',
          boxShadow: '0 0 40px rgba(34,197,94,0.2)',
        }}
        initial={{ scale: 0 }}
        animate={{ scale: 1 }}
        transition={{ type: 'spring', duration: 0.6 }}
      >
        <Check size={40} className="text-charlie-green" />
      </motion.div>

      <div>
        <h2 className="font-display text-2xl text-charlie-cyan tracking-wide mb-2">You&apos;re All Set</h2>
        <p className="text-charlie-dim text-sm font-body">CHARLIE is ready to assist you</p>
      </div>

      {/* Summary */}
      <div className="glass-card p-4 rounded-lg text-left max-w-sm mx-auto space-y-2">
        <SummaryRow label="LLM" value={`${data.llm_provider} / ${data.llm_model}`} />
        <SummaryRow label="Voice" value={data.voice_enabled ? `Enabled (${data.wake_word})` : 'Disabled'} />
        <SummaryRow label="Integrations" value={connectedIntegrations.length > 0 ? connectedIntegrations.join(', ') : 'None'} />
        <SummaryRow label="Security" value={`TIER ${data.risk_tier} · Guardian ${data.guardian_enabled ? 'ON' : 'OFF'}`} />
      </div>

      <motion.div
        initial={{ opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.5 }}
        className="flex items-center justify-center gap-2 text-charlie-cyan"
      >
        <Rocket size={18} />
        <span className="font-display tracking-wider">Launching CHARLIE...</span>
      </motion.div>
    </div>
  )
}

function SummaryRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between text-xs">
      <span className="text-charlie-dim font-display tracking-wider uppercase">{label}</span>
      <span className="text-charlie-text font-body">{value}</span>
    </div>
  )
}
