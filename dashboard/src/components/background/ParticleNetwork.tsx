'use client'

import { useEffect, useRef } from 'react'

interface Particle {
  x: number
  y: number
  vx: number
  vy: number
  radius: number
}

export function ParticleNetwork() {
  const canvasRef = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return

    const ctx = canvas.getContext('2d')
    if (!ctx) return

    // Respect reduced motion
    const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    if (prefersReducedMotion) return

    let animationId: number
    let particles: Particle[] = []
    const PARTICLE_COUNT = 60
    const CONNECTION_DISTANCE = 120
    const SPEED = 0.3

    function resize() {
      canvas!.width = window.innerWidth
      canvas!.height = window.innerHeight
    }

    function initParticles() {
      particles = Array.from({ length: PARTICLE_COUNT }, () => ({
        x: Math.random() * canvas!.width,
        y: Math.random() * canvas!.height,
        vx: (Math.random() - 0.5) * SPEED,
        vy: (Math.random() - 0.5) * SPEED,
        radius: Math.random() * 1.5 + 0.5,
      }))
    }

    function draw() {
      ctx!.clearRect(0, 0, canvas!.width, canvas!.height)

      // Draw connections
      for (let i = 0; i < particles.length; i++) {
        for (let j = i + 1; j < particles.length; j++) {
          const dx = particles[i].x - particles[j].x
          const dy = particles[i].y - particles[j].y
          const dist = Math.sqrt(dx * dx + dy * dy)

          if (dist < CONNECTION_DISTANCE) {
            const isLight = document.documentElement.classList.contains('light')
            const baseColor = isLight ? '100, 116, 139' : '0, 212, 255'
            const opacity = (1 - dist / CONNECTION_DISTANCE) * (isLight ? 0.08 : 0.15)
            ctx!.strokeStyle = `rgba(${baseColor}, ${opacity})`
            ctx!.lineWidth = 0.5
            ctx!.beginPath()
            ctx!.moveTo(particles[i].x, particles[i].y)
            ctx!.lineTo(particles[j].x, particles[j].y)
            ctx!.stroke()
          }
        }
      }

      // Draw particles
      for (const p of particles) {
        ctx!.beginPath()
        ctx!.arc(p.x, p.y, p.radius, 0, Math.PI * 2)
        const isLight = document.documentElement.classList.contains('light')
        ctx!.fillStyle = isLight ? 'rgba(100, 116, 139, 0.2)' : 'rgba(0, 212, 255, 0.4)'
        ctx!.fill()
      }
    }

    function update() {
      for (const p of particles) {
        p.x += p.vx
        p.y += p.vy

        if (p.x < 0 || p.x > canvas!.width) p.vx *= -1
        if (p.y < 0 || p.y > canvas!.height) p.vy *= -1
      }
    }

    function animate() {
      update()
      draw()
      animationId = requestAnimationFrame(animate)
    }

    resize()
    initParticles()
    animate()

    window.addEventListener('resize', () => {
      resize()
      initParticles()
    })

    return () => {
      cancelAnimationFrame(animationId)
    }
  }, [])

  return (
    <canvas
      ref={canvasRef}
      className="fixed inset-0 pointer-events-none z-0"
      style={{ opacity: 0.6 }}
    />
  )
}
