import { type HTMLAttributes } from 'react'

/** design.json authPatterns.errorHandling + emptyLoadingErrorStates.error */
const containerClass =
  'rounded-card border border-semantic-error/30 bg-semantic-error/5 p-6'
const titleClass = 'text-sm font-semibold text-semantic-error'
const bodyClass = 'mt-1 text-sm text-text-secondary'

export interface ErrorBoxProps extends HTMLAttributes<HTMLDivElement> {
  title?: string
  message: string
}

export function ErrorBox({ title = 'Error', message, className = '', ...props }: ErrorBoxProps) {
  return (
    <div
      className={`${containerClass} ${className}`.trim()}
      role="alert"
      {...props}
    >
      <p className={titleClass}>{title}</p>
      <p className={bodyClass}>{message}</p>
    </div>
  )
}
