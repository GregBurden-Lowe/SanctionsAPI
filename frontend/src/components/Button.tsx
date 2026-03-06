import { type ButtonHTMLAttributes, forwardRef } from 'react'

const base =
  'inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-lg font-medium transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand focus-visible:ring-offset-2 focus-visible:ring-offset-app disabled:opacity-40 disabled:pointer-events-none'
const sizes = {
  sm: 'h-8 px-3.5 text-xs',
  md: 'h-10 px-3.5 text-[13px]',
} as const
const variants = {
  primary: 'bg-accent text-white border border-transparent hover:bg-[#2d5986] active:translate-y-px',
  secondary: 'bg-secondary text-[#475569] border border-border hover:bg-brand/10 hover:text-brand active:translate-y-px',
  ghost: 'bg-secondary text-[#475569] border border-border hover:bg-brand/10 hover:text-brand active:translate-y-px',
  icon: 'h-9 w-9 p-0 rounded-lg border border-border bg-surface text-[#475569] hover:bg-muted active:translate-y-px',
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
