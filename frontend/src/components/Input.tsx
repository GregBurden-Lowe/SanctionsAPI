import { forwardRef, type InputHTMLAttributes } from 'react'

const base =
  'w-full h-10 rounded-lg border bg-surface px-3 text-sm text-text-primary placeholder:text-text-muted outline-none transition focus:border-brand focus:ring-2 focus:ring-brand/15'
const states = {
  default: 'border-border',
  error: 'border-semantic-error focus:border-semantic-error focus:ring-2 focus:ring-semantic-error/15',
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
      : 'block text-xs font-medium text-text-primary mb-1'
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
