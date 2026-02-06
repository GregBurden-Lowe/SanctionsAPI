/** Tailwind theme from design.json — do not add tokens outside this file. */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        app: '#F6F6EE',
        surface: '#FFFFFF',
        border: '#E5E5E0',
        text: {
          primary: '#111111',
          secondary: '#555555',
          muted: '#8A8A8A',
        },
        brand: {
          DEFAULT: '#111111',
        },
        semantic: {
          success: '#2E7D32',
          warning: '#ED6C02',
          error: '#D32F2F',
          info: '#0288D1',
        },
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', 'BlinkMacSystemFont', 'Segoe UI', 'sans-serif'],
      },
      borderRadius: {
        card: '12px',
        modal: '16px',
      },
      boxShadow: {
        card: '0 1px 2px rgba(0,0,0,0.04)',
      },
    },
  },
  plugins: [],
}
