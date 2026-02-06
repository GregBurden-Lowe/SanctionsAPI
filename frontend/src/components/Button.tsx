import { type ButtonHTMLAttributes, forwardRef } from 'react'

const base =
  'inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-lg font-medium transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand focus-visible:ring-offset-2 focus-visible:ring-offset-app disabled:opacity-40 disabled:pointer-events-none'
const sizes = {
  sm: 'h-8 px-3 text-xs',
  md: 'h-10 px-4 text-sm',
} as const
const variants = {
  primary: 'bg-brand text-white hover:opacity-90 active:translate-y-px',
  secondary: 'bg-surface text-text-primary border border-border hover:bg-app active:translate-y-px',
  ghost: 'bg-transparent text-text-primary hover:bg-app active:translate-y-px',
  icon: 'h-10 w-10 p-0 rounded-full hover:bg-app active:translate-y-px',
} as const

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: keyof typeof variants
  size?: keyof typeof sizes
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className = '', variant = 'primary', size = 'md', ...props }, ref) => (
    <button
      ref={ref}
      className={`${base} ${sizes[size]} ${variants[variant]} ${className}`.trim()}
      {...props}
    />
  )
)
Button.displayName = 'Button'
