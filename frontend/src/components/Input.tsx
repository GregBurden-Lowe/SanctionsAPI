import { forwardRef, type InputHTMLAttributes } from 'react'

const base =
  'w-full h-10 rounded-lg border bg-[#f8fafc] px-3 text-[13px] text-[#1e293b] placeholder:text-[#94a3b8] outline-none transition-all duration-150 focus:border-[#3b82f6] focus:ring-0'
const states = {
  default: 'border-[#e2e8f0]',
  error: 'border-semantic-error focus:border-semantic-error',
}

export interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  label: string
  error?: string
  /** When true, label is sr-only (accessibility only) */
  hideLabel?: boolean
}

export const Input = forwardRef<HTMLInputElement, InputProps>(
  ({ label, error, hideLabel, className = '', id, ...props }, ref) => {
    const inputId = id ?? `input-${label.replace(/\s/g, '-')}`
    const labelClass = hideLabel
      ? 'sr-only'
      : 'block text-[12.5px] font-semibold text-[#374151] mb-[5px]'
    return (
      <div className="space-y-2">
        <label htmlFor={inputId} className={labelClass}>
          {label}
        </label>
        <input
          ref={ref}
          id={inputId}
          {...(error && { 'aria-invalid': 'true' as const })}
          aria-describedby={error ? `${inputId}-error` : undefined}
          className={`${base} ${error ? states.error : states.default} ${className}`.trim()}
          {...props}
        />
        {error && (
          <p id={`${inputId}-error`} className="text-xs text-semantic-error" role="alert">
            {error}
          </p>
        )}
      </div>
    )
  }
)
Input.displayName = 'Input'
