/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      // ── CSS-variable-based semantic tokens ────────────────────────
      // These respond to [data-theme] attribute changes at runtime.
      colors: {
        // Body + structural backgrounds
        bg:          'var(--bg)',
        surface:     'var(--surface)',
        'surface-2': 'var(--surface-2)',
        'surface-3': 'var(--surface-3)',

        // Borders
        'border-subtle': 'var(--border)',
        'border-mid':    'var(--border-2)',
        'border-strong': 'var(--border-3)',

        // Accent
        accent:      'var(--accent)',
        'accent-dim': 'var(--accent-dim)',

        // Text
        text:        'var(--text)',
        'text-2':    'var(--text-2)',
        'text-3':    'var(--text-3)',

        // Signal colors
        'sig-green': 'var(--green)',
        'sig-red':   'var(--red)',
        'sig-amber': 'var(--amber)',
        'sig-blue':  'var(--blue)',

        // ── Legacy compat (used in pages not yet rewritten) ─────────
        primary:                 'var(--accent)',
        secondary:               'var(--text-2)',
        background:              'var(--bg)',
        'on-surface':            'var(--text)',
        'surface-container':     'var(--surface-2)',
        'surface-container-low': 'var(--surface)',
        'surface-container-high':'var(--surface-3)',
        'surface-dim':           'var(--bg)',
        'surface-bright':        'var(--surface-3)',
        'surface-variant':       'var(--border)',
        'on-surface-variant':    'var(--text-2)',
        outline:                 'var(--border-2)',
        'outline-variant':       'var(--border)',
        'primary-container':     'var(--accent)',
        'on-primary':            'var(--bg)',
        'primary-fixed':         'var(--accent-muted)',
        'on-primary-fixed':      'var(--text)',
        error:                   'var(--red)',
        'error-container':       'var(--red-bg)',
        'on-error':              'var(--bg)',
        tertiary:                'var(--amber)',
        'inverse-surface':       'var(--text)',
        'inverse-on-surface':    'var(--bg)',
      },

      fontFamily: {
        sans:     ['Syne', 'system-ui', 'sans-serif'],
        body:     ['Syne', 'system-ui', 'sans-serif'],
        label:    ['Syne', 'system-ui', 'sans-serif'],
        headline: ['Syne', 'sans-serif'],
        mono:     ['JetBrains Mono', 'Consolas', 'monospace'],
        data:     ['JetBrains Mono', 'Consolas', 'monospace'],
      },

      borderRadius: {
        DEFAULT: '0.125rem',
        sm:   '0.25rem',
        md:   '0.375rem',
        lg:   '0.5rem',
        xl:   '0.75rem',
        '2xl':'1rem',
        full: '9999px',
      },

      boxShadow: {
        card:         'var(--card-shadow)',
        'glow-amber': '0 0 16px rgba(233,163,0,0.25), 0 0 4px rgba(233,163,0,0.15)',
        'glow-green': '0 0 16px rgba(0,217,138,0.25)',
        'glow-red':   '0 0 16px rgba(255,51,71,0.30)',
      },

      animation: {
        'slide-down': 'slideDown 0.2s ease-out',
        'fade-in':    'fadeIn 0.3s ease-out',
      },

      keyframes: {
        slideDown: {
          from: { opacity: '0', transform: 'translateY(-6px)' },
          to:   { opacity: '1', transform: 'translateY(0)' },
        },
        fadeIn: {
          from: { opacity: '0' },
          to:   { opacity: '1' },
        },
      },
    },
  },
  plugins: [],
}
