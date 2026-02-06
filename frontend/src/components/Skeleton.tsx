import { type HTMLAttributes } from 'react'

/** design.json emptyLoadingErrorStates.loading */
const skeletonClass = 'animate-pulse rounded-lg bg-app'

export function Skeleton({
  className = '',
  ...props
}: HTMLAttributes<HTMLDivElement>) {
  return <div className={`${skeletonClass} ${className}`.trim()} {...props} />
}
