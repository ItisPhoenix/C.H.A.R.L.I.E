'use client'

import { motion } from 'framer-motion'

export function WelcomeStep() {
  return (
    <div className="text-center space-y-8">
      {/* Animated orb */}
      <motion.div
        className="mx-auto w-32 h-32 rounded-full relative"
        style={{
          background: 'radial-gradient(circle, rgba(0,212,255,0.3) 0%, transparent 70%)',
          boxShadow: '0 0 60px rgba(0,212,255,0.2), 0 0 120px rgba(0,212,255,0.1)',
        }}
        animate={{
          scale: [1, 1.05, 1],
          boxShadow: [
            '0 0 60px rgba(0,212,255,0.2), 0 0 120px rgba(0,212,255,0.1)',
            '0 0 80px rgba(0,212,255,0.3), 0 0 160px rgba(0,212,255,0.15)',
            '0 0 60px rgba(0,212,255,0.2), 0 0 120px rgba(0,212,255,0.1)',
          ],
        }}
        transition={{ duration: 3, repeat: Infinity, ease: 'easeInOut' }}
      >
        <div className="absolute inset-4 rounded-full border border-charlie-cyan/30" />
        <div className="absolute inset-8 rounded-full border border-charlie-cyan/20" />
        <div className="absolute inset-0 flex items-center justify-center">
          <span className="font-display text-3xl text-charlie-cyan neon-text">C</span>
        </div>
      </motion.div>

      <div>
        <h1 className="font-display text-4xl text-charlie-cyan neon-text-strong tracking-wider mb-4">
          CHARLIE
        </h1>
        <p className="font-body text-lg text-charlie-text max-w-md mx-auto">
          Your personal AI assistant. Let&apos;s get you set up in about 2 minutes.
        </p>
      </div>

      <div className="grid grid-cols-3 gap-4 max-w-lg mx-auto text-center">
        {[
          { icon: '🧠', label: 'LLM Brain' },
          { icon: '🎙️', label: 'Voice' },
          { icon: '🔗', label: 'Integrations' },
        ].map((item, i) => (
          <motion.div
            key={item.label}
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.5 + i * 0.15 }}
            className="glass-card p-3 rounded-lg"
          >
            <div className="text-2xl mb-1">{item.icon}</div>
            <div className="text-charlie-dim text-xs font-body">{item.label}</div>
          </motion.div>
        ))}
      </div>
    </div>
  )
}
