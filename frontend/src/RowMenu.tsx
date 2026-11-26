import { useEffect, useRef, useState } from 'react'
import { Icon } from './Icon'

/** The "⋯" menu at the end of a list row (lectures, folders, past exams, quizzes, cheatsheets). */
export type MenuItem = { label: string; onClick: () => void; disabled?: boolean }

export function RowMenu({ label, items }: { label: string; items: MenuItem[] }) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const close = (e: Event) => {
      if (e instanceof KeyboardEvent ? e.key === 'Escape' : !ref.current?.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('pointerdown', close)
    document.addEventListener('keydown', close)
    return () => {
      document.removeEventListener('pointerdown', close)
      document.removeEventListener('keydown', close)
    }
  }, [open])
  return (
    <div className="menu row-menu" ref={ref}>
      <button className="btn ghost icon-btn" aria-label={label} title={label} aria-expanded={open}
        onClick={() => setOpen((o) => !o)}><Icon name="more" /></button>
      {open && (
        <div className="menu-pop list" role="menu">
          {items.map((it) => (
            <button key={it.label} role="menuitem" className="btn ghost" disabled={it.disabled}
              onClick={() => { setOpen(false); it.onClick() }}>{it.label}</button>
          ))}
        </div>
      )}
    </div>
  )
}
