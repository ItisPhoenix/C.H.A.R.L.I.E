/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    './src/app/**/*.{js,ts,jsx,tsx,mdx}',
    './src/components/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        'charlie-cyan': 'var(--charlie-cyan)',
        'charlie-dark': 'var(--charlie-dark)',
        'charlie-card': 'var(--charlie-card)',
        'charlie-border': 'var(--charlie-border)',
        'charlie-text': 'var(--charlie-text)',
        'charlie-dim': 'var(--charlie-dim)',
        'charlie-purple': 'var(--charlie-purple)',
        'charlie-teal': 'var(--charlie-teal)',
        'charlie-amber': 'var(--charlie-amber)',
        'charlie-orange': 'var(--charlie-orange)',
        'charlie-red': 'var(--charlie-red)',
        'charlie-green': 'var(--charlie-green)',
        'charlie-bg': 'var(--charlie-card)',
        'voice-listening': 'var(--voice-listening)',
        'voice-processing': 'var(--voice-processing)',
        'voice-speaking': 'var(--voice-speaking)',
        'voice-idle': 'var(--voice-idle)',
      },
      fontFamily: {
        sans: ['var(--font-sans)', 'system-ui', 'sans-serif'],
        display: ['var(--font-sans)', 'system-ui', 'sans-serif'],
        body: ['var(--font-sans)', 'system-ui', 'sans-serif'],
        mono: ['var(--font-mono)', 'Consolas', 'monospace'],
      },
      boxShadow: {
        'premium': '0 4px 24px -4px rgba(0, 0, 0, 0.4)',
        'glow': '0 0 12px rgba(255, 255, 255, 0.1)',
        'inner-light': 'inset 0 1px 0 0 rgba(255, 255, 255, 0.05)',
        // NOTE: rgba(136, 204, 255, ...) values are dark-mode only (charlie-cyan #88ccff).
        // Tailwind config cannot use CSS var() in boxShadow, so these remain hardcoded.
        'neon-glow': '0 0 10px rgba(136, 204, 255, 0.15), 0 0 30px rgba(136, 204, 255, 0.1)',
        'neon-glow-strong': '0 0 20px rgba(136, 204, 255, 0.3), 0 0 60px rgba(136, 204, 255, 0.15)',
        'neon-cyan': '0 0 10px rgba(136, 204, 255, 0.2)',
        'neon-cyan-sm': '0 0 6px rgba(136, 204, 255, 0.15)',
      },
      animation: {
        'pulse-slow': 'pulse 3s ease-in-out infinite',
        'glow': 'glow 2s ease-in-out infinite alternate',
        'slide-in': 'slideIn 0.3s ease-out',
        'slide-in-right': 'slideInRight 0.3s ease-out',
        'fade-in': 'fadeIn 0.2s ease-out',
        'fade-in-up': 'fadeInUp 0.3s ease-out',
        'voice-pulse': 'voicePulse 1.5s ease-in-out infinite',
        'scanline': 'scanline 8s linear infinite',
        'glitch': 'glitch 0.5s ease-in-out',
        'neon-pulse': 'neonPulse 2s ease-in-out infinite',
        'hex-spin': 'hexSpin 20s linear infinite',
        'stagger': 'fadeInUp 0.3s ease-out both',
      },
      keyframes: {
        // NOTE: rgba(136, 204, 255, ...) values are dark-mode only (charlie-cyan #88ccff).
        // Tailwind keyframes cannot use CSS var() in box-shadow values.
        glow: {
          '0%': { boxShadow: '0 0 5px rgba(136, 204, 255, 0.2)' },
          '100%': { boxShadow: '0 0 20px rgba(136, 204, 255, 0.4)' },
        },
        slideIn: {
          '0%': { opacity: '0', transform: 'translateY(-10px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        slideInRight: {
          '0%': { opacity: '0', transform: 'translateX(100%)' },
          '100%': { opacity: '1', transform: 'translateX(0)' },
        },
        fadeIn: {
          '0%': { opacity: '0' },
          '100%': { opacity: '1' },
        },
        fadeInUp: {
          '0%': { opacity: '0', transform: 'translateY(12px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        voicePulse: {
          '0%, 100%': { opacity: '0.8', transform: 'scale(1)', boxShadow: '0 0 10px var(--tw-shadow-color)' },
          '50%': { opacity: '1', transform: 'scale(1.05)', boxShadow: '0 0 25px var(--tw-shadow-color)' },
        },
        scanline: {
          '0%': { transform: 'translateY(-100%)' },
          '100%': { transform: 'translateY(100vh)' },
        },
        glitch: {
          '0%': { transform: 'translate(0)' },
          '20%': { transform: 'translate(-2px, 2px)' },
          '40%': { transform: 'translate(-2px, -2px)' },
          '60%': { transform: 'translate(2px, 2px)' },
          '80%': { transform: 'translate(2px, -2px)' },
          '100%': { transform: 'translate(0)' },
        },
        // NOTE: rgba(136, 204, 255, ...) values are dark-mode only (charlie-cyan #88ccff).
        neonPulse: {
          '0%, 100%': { boxShadow: '0 0 10px rgba(136, 204, 255, 0.3)' },
          '50%': { boxShadow: '0 0 25px rgba(136, 204, 255, 0.6)' },
        },
        'slide-in': {
          '0%': { opacity: '0', transform: 'scale(0.95) translateY(10px)' },
          '100%': { opacity: '1', transform: 'scale(1) translateY(0)' },
        },
        'slide-up': {
          '0%': { opacity: '0', transform: 'translateY(10px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        hexSpin: {
          '0%': { transform: 'rotate(0deg)' },
          '100%': { transform: 'rotate(360deg)' },
        },
      },
    },
  },
  plugins: [],
}
