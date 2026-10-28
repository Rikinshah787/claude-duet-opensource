import type { Config } from 'tailwindcss';

const config: Config = {
  content: ['./app/**/*.{ts,tsx}', './components/**/*.{ts,tsx}', './lib/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        canvas: 'rgb(var(--canvas) / <alpha-value>)',
        surface: 'rgb(var(--surface) / <alpha-value>)',
        raised: 'rgb(var(--raised) / <alpha-value>)',
        hairline: 'var(--border)',
        'hairline-strong': 'var(--border-strong)',
        ink: {
          DEFAULT: 'rgb(var(--text) / <alpha-value>)',
          muted: 'rgb(var(--text-2) / <alpha-value>)',
          faint: 'rgb(var(--text-3) / <alpha-value>)',
        },
        accent: {
          DEFAULT: 'rgb(var(--accent) / <alpha-value>)',
          ink: 'rgb(var(--accent-ink) / <alpha-value>)',
        },
        risk: {
          safe: 'rgb(var(--safe) / <alpha-value>)',
          review: 'rgb(var(--review) / <alpha-value>)',
          sensitive: 'rgb(var(--sensitive) / <alpha-value>)',
          forbidden: 'rgb(var(--forbidden) / <alpha-value>)',
        },
      },
      borderColor: {
        DEFAULT: 'var(--border)',
      },
      fontFamily: {
        serif: ['ui-serif', 'Georgia', 'Times New Roman', 'serif'],
        sans: [
          'ui-sans-serif',
          'system-ui',
          '-apple-system',
          'Segoe UI',
          'Helvetica Neue',
          'Arial',
          'sans-serif',
        ],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'Consolas', 'monospace'],
      },
      borderRadius: {
        xl: '0.875rem',
      },
      maxWidth: {
        reading: '46rem',
      },
      keyframes: {
        rise: {
          '0%': { opacity: '0', transform: 'translateY(4px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
      },
      animation: {
        rise: 'rise 200ms ease-out both',
      },
    },
  },
  plugins: [],
};

export default config;
