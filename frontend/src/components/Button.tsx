import { type ButtonHTMLAttributes, forwardRef } from 'react'

const base =
  'inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-lg font-semibold transition-all duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand focus-visible:ring-offset-2 focus-visible:ring-offset-app disabled:opacity-40 disabled:pointer-events-none'
const sizes = {
  sm: 'h-8 px-[10px] text-[11.5px]',
  md: 'h-9 px-[14px] text-[12.5px]',
} as const
const variants = {
  primary: 'bg-[#1e3a5f] text-white border border-transparent hover:bg-[#2d5986] active:translate-y-px',
  secondary: 'bg-[#f8fafc] text-[#475569] border border-[#e2e8f0] hover:bg-[#f1f5f9] hover:border-[#cbd5e1] active:translate-y-px',
  ghost: 'bg-[#f8fafc] text-[#475569] border border-[#e2e8f0] hover:bg-[#f1f5f9] hover:border-[#cbd5e1] active:translate-y-px',
  icon: 'h-9 w-9 p-0 rounded-lg border border-[#e2e8f0] bg-white text-[#64748b] hover:bg-[#f1f5f9] active:translate-y-px',
  destructive:
    'bg-[rgba(220,60,60,0.08)] text-[#d94040] border border-[rgba(220,60,60,0.2)] hover:bg-[rgba(220,60,60,0.14)] active:translate-y-px',
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
