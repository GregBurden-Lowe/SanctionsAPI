import { type HTMLAttributes } from 'react'

const containerClass = 'flex items-center justify-between gap-4'
const titleClass = 'text-page-title'
const metaClass = 'text-[13px] font-normal text-[#64748b]'

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
