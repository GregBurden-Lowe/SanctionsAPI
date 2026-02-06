import { type HTMLAttributes } from 'react'

const containerClass = 'flex items-center justify-between'
const titleClass = 'text-base font-semibold text-text-primary'
const metaClass = 'text-xs font-medium text-text-muted'

export function SectionHeader({
  title,
  meta,
  className = '',
  ...props
}: HTMLAttributes<HTMLDivElement> & { title: React.ReactNode; meta?: React.ReactNode }) {
  return (
    <div className={`${containerClass} ${className}`.trim()} {...props}>
      <h2 className={titleClass}>{title}</h2>
      {meta != null && <span className={metaClass}>{meta}</span>}
    </div>
  )
}
