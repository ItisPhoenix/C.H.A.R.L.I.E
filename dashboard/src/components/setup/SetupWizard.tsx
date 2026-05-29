'use client'

import { useState, useEffect } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { ChevronLeft, ChevronRight, Rocket } from 'lucide-react'
import { WelcomeStep } from './steps/WelcomeStep'
import { LLMStep } from './steps/LLMStep'
import { VoiceStep } from './steps/VoiceStep'
import { IntegrationsStep } from './steps/IntegrationsStep'
import { SecurityStep } from './steps/SecurityStep'
import { CompleteStep } from './steps/CompleteStep'
import { ParticleNetwork } from '@/components/background/ParticleNetwork'
import { HexGrid } from '@/components/background/HexGrid'
import { type SetupData, defaultSetupData } from './types'

const STEPS = [
  { id: 'welcome', label: 'Welcome', component: WelcomeStep },
  { id: 'llm', label: 'LLM', component: LLMStep },
  { id: 'voice', label: 'Voice', component: VoiceStep },
  { id: 'integrations', label: 'Integrations', component: IntegrationsStep },
  { id: 'security', label: 'Security', component: SecurityStep },
  { id: 'complete', label: 'Complete', component: CompleteStep },
]

export function SetupWizard({ onComplete }: { onComplete: (data: SetupData) => void }) {
  const [currentStep, setCurrentStep] = useState(0)
  const [data, setData] = useState<SetupData>(defaultSetupData)
  const [direction, setDirection] = useState(1)

  const StepComponent = STEPS[currentStep].component
  const isLast = currentStep === STEPS.length - 1
  const isFirst = currentStep === 0

  function next() {
    if (isLast) {
      onComplete(data)
      return
    }
    setDirection(1)
    setCurrentStep((s) => s + 1)
  }

  function prev() {
    if (isFirst) return
    setDirection(-1)
    setCurrentStep((s) => s - 1)
  }

  function skip() {
    onComplete(data)
  }

  return (
    <div className="fixed inset-0 z-50 bg-charlie-dark flex flex-col">
      {/* Background */}
      <ParticleNetwork />
      <HexGrid />

      {/* Progress bar */}
      <div className="relative z-10 px-8 pt-6">
        <div className="flex items-center gap-2 max-w-2xl mx-auto">
          {STEPS.map((step, i) => (
            <div key={step.id} className="flex-1 flex items-center gap-2">
              <div
                className={`h-1 flex-1 rounded-full transition-all duration-500 ${
                  i <= currentStep
                    ? 'bg-charlie-cyan shadow-neon-cyan-sm'
                    : 'bg-charlie-border'
                }`}
              />
              <span
                className={`text-xs font-display tracking-wider transition-colors ${
                  i === currentStep ? 'text-charlie-cyan' : 'text-charlie-dim'
                }`}
              >
                {step.label}
              </span>
            </div>
          ))}
        </div>
      </div>

      {/* Step content */}
      <div className="flex-1 flex items-center justify-center relative z-10 px-8">
        <AnimatePresence mode="wait" custom={direction}>
          <motion.div
            key={currentStep}
            custom={direction}
            initial={{ opacity: 0, x: direction * 100 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: -direction * 100 }}
            transition={{ duration: 0.3, ease: 'easeOut' }}
            className="w-full max-w-2xl"
          >
            <StepComponent data={data} onChange={setData} />
          </motion.div>
        </AnimatePresence>
      </div>

      {/* Navigation */}
      <div className="relative z-10 px-8 pb-8 flex items-center justify-between max-w-2xl mx-auto w-full">
        <button
          onClick={prev}
          disabled={isFirst}
          className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-body text-charlie-dim hover:text-charlie-text disabled:opacity-30 disabled:cursor-not-allowed transition-colors cursor-pointer"
        >
          <ChevronLeft size={16} /> Back
        </button>

        <button
          onClick={skip}
          className="text-charlie-dim text-xs hover:text-charlie-text transition-colors cursor-pointer"
        >
          Skip setup
        </button>

        <button
          onClick={next}
          className="flex items-center gap-2 px-6 py-2.5 rounded-lg text-sm font-display tracking-wide bg-charlie-cyan/15 text-charlie-cyan border border-charlie-cyan/30 hover:bg-charlie-cyan/25 hover:shadow-neon-cyan-sm transition-all cursor-pointer"
        >
          {isLast ? (
            <>
              <Rocket size={16} /> Launch CHARLIE
            </>
          ) : (
            <>
              Next <ChevronRight size={16} />
            </>
          )}
        </button>
      </div>
    </div>
  )
}
