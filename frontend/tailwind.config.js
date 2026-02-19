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
        sans: ['MediumLL', 'MediumLL Fallback', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        serif: ['IvoryLL', 'IvoryLL Fallback', 'ui-serif', 'Georgia', 'Cambria'],
        mono: ['DmMono', 'DmMono Fallback', 'ui-monospace', 'monospace'],
      },
      borderRadius: {
        card: '0.375rem',
        modal: '0.375rem',
      },
      boxShadow: {
        card: '0px 4px 37px 0px rgba(0, 0, 0, 0.05)',
        sm: '0px 4px 37px 0px rgba(0, 0, 0, 0.05)',
        md: '0px 10px 84px 0px rgba(0, 0, 0, 0.1)',
        lg: '0px 10px 84px 0px rgba(0, 0, 0, 0.15)',
        xl: '0px 10px 84px 0px rgba(0, 0, 0, 0.25)',
      },
    },
  },
  plugins: [],
}
