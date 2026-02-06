import { useEffect } from 'react'

/** design.json Modal: overlay, panel, header, title, body, footer */
const overlayClass = 'fixed inset-0 bg-black/40'
const panelClass =
  'fixed left-1/2 top-1/2 w-[min(560px,calc(100%-2rem))] -translate-x-1/2 -translate-y-1/2 rounded-modal border border-border bg-surface p-6 shadow-xl'
const headerClass = 'mb-4 flex items-center justify-between'
const titleClass = 'text-lg font-semibold text-text-primary'
const bodyClass = 'text-sm text-text-secondary'
const footerClass = 'mt-6 flex justify-end gap-3'

export interface ModalProps {
  isOpen: boolean
  onClose: () => void
  title: string
  children: React.ReactNode
  footer?: React.ReactNode
}

export function Modal({ isOpen, onClose, title, children, footer }: ModalProps) {
  useEffect(() => {
    if (!isOpen) return
    const handleEscape = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', handleEscape)
    return () => document.removeEventListener('keydown', handleEscape)
  }, [isOpen, onClose])

  if (!isOpen) return null

  return (
    <div
      className={overlayClass}
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-labelledby="modal-title"
    >
      <div
        className={panelClass}
        onClick={(e) => e.stopPropagation()}
      >
        <div className={headerClass}>
          <h2 id="modal-title" className={titleClass}>
            {title}
          </h2>
        </div>
        <div className={bodyClass}>{children}</div>
        {footer != null && <div className={footerClass}>{footer}</div>}
      </div>
    </div>
  )
}
