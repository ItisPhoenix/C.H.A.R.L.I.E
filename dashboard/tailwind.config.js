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
        'charlie-cyan': 'rgb(var(--charlie-cyan) / <alpha-value>)',
        'charlie-dark': 'rgb(var(--charlie-dark) / <alpha-value>)',
        'charlie-card': 'rgb(var(--charlie-card) / <alpha-value>)',
        'charlie-border': 'var(--charlie-border)',
        'charlie-text': 'rgb(var(--charlie-text) / <alpha-value>)',
        'charlie-dim': 'rgb(var(--charlie-dim) / <alpha-value>)',
        'charlie-purple': 'rgb(var(--charlie-purple) / <alpha-value>)',
        'charlie-teal': 'rgb(var(--charlie-teal) / <alpha-value>)',
        'charlie-amber': 'rgb(var(--charlie-amber) / <alpha-value>)',
        'charlie-orange': 'rgb(var(--charlie-orange) / <alpha-value>)',
        'charlie-red': 'rgb(var(--charlie-red) / <alpha-value>)',
        'charlie-green': 'rgb(var(--charlie-green) / <alpha-value>)',
        'charlie-bg': 'rgb(var(--charlie-card) / <alpha-value>)',
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
        'inner-light': 'inset 0 1px 0 0 rgba(255, 255, 255, 0.05)',
      },
      animation: {
        'pulse-slow': 'pulse 3s ease-in-out infinite',
        'slide-in': 'slideIn 0.3s ease-out',
        'slide-in-right': 'slideInRight 0.3s ease-out',
        'fade-in': 'fadeIn 0.2s ease-out',
        'fade-in-up': 'fadeInUp 0.3s ease-out',
        'voice-pulse': 'voicePulse 1.5s ease-in-out infinite',
        'stagger': 'fadeInUp 0.3s ease-out both',
      },
      keyframes: {
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
          '0%, 100%': { opacity: '0.8', transform: 'scale(1)' },
          '50%': { opacity: '1', transform: 'scale(1.05)' },
        },
      },
    },
  },
  plugins: [],
}
