import { type HTMLAttributes } from 'react'

const base = 'rounded-2xl border border-border bg-surface shadow-sm'
const padding = 'p-5'
const headerClass = 'flex items-start justify-between gap-3'
const titleClass = 'text-base font-semibold text-text-primary'
const bodyClass = 'mt-3 text-sm text-text-secondary leading-relaxed'
const footerClass = 'mt-4 flex items-center justify-between text-xs text-text-muted'

export interface CardProps extends HTMLAttributes<HTMLDivElement> {
  variant?: 'default' | 'selected' | 'inverse'
}

export function Card({ className = '', variant = 'default', ...props }: CardProps) {
  const variantClass =
    variant === 'selected'
      ? 'ring-2 ring-brand/15'
      : variant === 'inverse'
        ? 'bg-brand text-white border-transparent'
        : ''
  return (
    <div
      className={`${base} ${padding} ${variantClass} ${className}`.trim()}
      {...props}
    />
  )
}

export function CardHeader({
  className = '',
  ...props
}: HTMLAttributes<HTMLDivElement>) {
  return <div className={`${headerClass} ${className}`.trim()} {...props} />
}

export function CardTitle({
  className = '',
  ...props
}: HTMLAttributes<HTMLHeadingElement>) {
  return <h3 className={`${titleClass} ${className}`.trim()} {...props} />
}

export function CardBody({
  className = '',
  ...props
}: HTMLAttributes<HTMLDivElement>) {
  return <div className={`${bodyClass} ${className}`.trim()} {...props} />
}

export function CardFooter({
  className = '',
  ...props
}: HTMLAttributes<HTMLDivElement>) {
  return <div className={`${footerClass} ${className}`.trim()} {...props} />
}
