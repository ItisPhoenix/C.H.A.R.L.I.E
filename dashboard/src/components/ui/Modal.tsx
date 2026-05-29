'use client'

import { cn } from '@/lib/utils'

interface ModalProps {
  open: boolean
  onClose: () => void
  title: string
  children: React.ReactNode
  className?: string
}

export function Modal({ open, onClose, title, children, className }: ModalProps) {
  if (!open) return null
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={onClose} />
      <div
        className={cn(
          'relative glass-card p-6 max-w-md w-full mx-4 animate-slide-in',
          'border-charlie-cyan/25 shadow-neon-cyan',
          className,
        )}
      >
        {/* Scanline header overlay */}
        <div
          className="absolute top-0 left-0 right-0 h-12 pointer-events-none rounded-t-xl"
          style={{
            background: 'repeating-linear-gradient(0deg, transparent, transparent 2px, rgba(0, 212, 255, 0.03) 2px, rgba(0, 212, 255, 0.03) 4px)',
          }}
        />
        <div className="relative flex items-center justify-between mb-4">
          <h2 className="font-display text-charlie-cyan font-bold tracking-wide">{title}</h2>
          <button
            onClick={onClose}
            className="text-charlie-dim hover:text-charlie-text transition-colors cursor-pointer"
            aria-label="Close modal"
          >
            <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
        {children}
      </div>
    </div>
  )
}
