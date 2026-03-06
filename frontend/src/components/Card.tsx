import { type HTMLAttributes } from 'react'

const base = 'rounded-[13px] border border-border bg-surface shadow-none overflow-hidden'
const padding = ''
const headerClass = 'flex items-center justify-between gap-3 px-5 py-[13px] border-b border-[#f1f5f9]'
const titleClass = 'text-section-title text-text-primary inline-flex items-center gap-2 before:content-[\'\'] before:w-2 before:h-2 before:rounded-full before:bg-brand before:shadow-[0_0_0_4px_rgba(59,130,246,0.15)]'
const bodyClass = 'px-5 py-4 text-[13px] text-text-secondary leading-relaxed'
const footerClass = 'px-5 pb-4 pt-2 flex items-center justify-between text-xs text-text-muted'

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
