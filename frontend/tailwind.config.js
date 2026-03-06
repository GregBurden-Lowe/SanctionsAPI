/** Tailwind theme from design.json — do not add tokens outside this file. */
const withOpacity = (cssVar) => `rgb(var(${cssVar}) / <alpha-value>)`

export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        app: withOpacity('--color-background'),
        surface: withOpacity('--color-card'),
        border: 'rgb(var(--color-border) / var(--color-border-alpha))',
        input: 'rgb(var(--color-input) / var(--color-input-alpha))',
        ring: withOpacity('--color-ring'),
        primary: withOpacity('--color-primary'),
        secondary: withOpacity('--color-secondary'),
        accent: withOpacity('--color-accent'),
        muted: withOpacity('--color-muted'),
        text: {
          primary: withOpacity('--color-foreground'),
          secondary: withOpacity('--color-secondary-foreground'),
          muted: withOpacity('--color-muted-foreground'),
        },
        brand: {
          DEFAULT: withOpacity('--color-primary'),
        },
        semantic: {
          success: withOpacity('--color-chart-3'),
          warning: withOpacity('--color-primary'),
          error: withOpacity('--color-destructive'),
          info: withOpacity('--color-accent'),
        },
      },
      fontFamily: {
        sans: ['DM Sans', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        serif: ['IvoryLL', 'IvoryLL Fallback', 'ui-serif', 'Georgia', 'Cambria'],
        mono: ['DM Mono', 'ui-monospace', 'monospace'],
      },
      borderRadius: {
        card: '13px',
        modal: '13px',
      },
      boxShadow: {
        card: 'none',
        sm: 'none',
        md: 'none',
        lg: 'none',
        xl: 'none',
      },
    },
  },
  plugins: [],
}
