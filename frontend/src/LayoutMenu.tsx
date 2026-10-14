import { useEffect, useRef } from 'react'
import { DEFAULT_LAYOUT, type Layout } from './geometry'
import { Icon } from './Icon'
import { useT } from './i18n'

const STACK_SVG = <svg viewBox="0 0 30 22" aria-hidden="true"><rect x="1" y="1" width="28" height="11" rx="2" fill="currentColor" opacity=".35" /><rect x="1" y="14" width="16" height="7" rx="1.5" fill="currentColor" opacity=".7" /><rect x="19" y="14" width="10" height="7" rx="1.5" fill="currentColor" opacity=".7" /></svg>
const SIDE_SVG = <svg viewBox="0 0 30 22" aria-hidden="true"><rect x="1" y="1" width="17" height="20" rx="2" fill="currentColor" opacity=".35" /><rect x="20" y="1" width="9" height="11" rx="1.5" fill="currentColor" opacity=".7" /><rect x="20" y="14" width="9" height="7" rx="1.5" fill="currentColor" opacity=".7" /></svg>

export function LayoutMenu({ layout, onChange }: { layout: Layout; onChange: (l: Layout) => void }) {
  const det = useRef<HTMLDetailsElement>(null)
  const t = useT()
  useEffect(() => {
    const close = (e: PointerEvent) => {
      if (det.current?.open && !det.current.contains(e.target as Node)) det.current.open = false
    }
    document.addEventListener('pointerdown', close)
    return () => document.removeEventListener('pointerdown', close)
  }, [])
  const mode = (m: Layout['mode'], label: string, svg: React.ReactNode) => (
    <button aria-pressed={layout.mode === m} onClick={() => onChange({ ...layout, mode: m })}>{svg}{label}</button>
  )
  const check = (key: 'showExplain' | 'showNotes', label: string) => (
    <label className="check">
      <input type="checkbox" checked={layout[key]} onChange={(e) => onChange({ ...layout, [key]: e.target.checked })} />
      {label}
    </label>
  )
  return (
    <details className="menu wide-only" ref={det}>
      <summary className="btn ghost" style={{ listStyle: 'none' }}><Icon name="layout" />{t.layout}</summary>
      <div className="menu-pop">
        <div><div className="lbl">{t.arrange}</div><div className="seg">{mode('stack', t.stack, STACK_SVG)}{mode('side', t.side, SIDE_SVG)}</div></div>
        <div><div className="lbl">{t.show}</div><div className="checks">{check('showExplain', t.explanation)}{check('showNotes', t.notes)}</div></div>
        <p className="tip">{t.layoutTip}</p>
        <button className="btn" onClick={() => onChange(structuredClone(DEFAULT_LAYOUT))}>{t.resetLayout}</button>
      </div>
    </details>
  )
}
