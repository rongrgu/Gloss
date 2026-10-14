import { useEffect, useRef } from 'react'
import { DEFAULT_LAYOUT, type Layout } from './geometry'
import { Icon } from './Icon'

const STACK_SVG = <svg viewBox="0 0 30 22" aria-hidden="true"><rect x="1" y="1" width="28" height="11" rx="2" fill="currentColor" opacity=".35" /><rect x="1" y="14" width="16" height="7" rx="1.5" fill="currentColor" opacity=".7" /><rect x="19" y="14" width="10" height="7" rx="1.5" fill="currentColor" opacity=".7" /></svg>
const SIDE_SVG = <svg viewBox="0 0 30 22" aria-hidden="true"><rect x="1" y="1" width="17" height="20" rx="2" fill="currentColor" opacity=".35" /><rect x="20" y="1" width="9" height="11" rx="1.5" fill="currentColor" opacity=".7" /><rect x="20" y="14" width="9" height="7" rx="1.5" fill="currentColor" opacity=".7" /></svg>

export function LayoutMenu({ layout, onChange }: { layout: Layout; onChange: (l: Layout) => void }) {
  const det = useRef<HTMLDetailsElement>(null)
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
      <summary className="btn ghost" style={{ listStyle: 'none' }}><Icon name="layout" />布局</summary>
      <div className="menu-pop">
        <div><div className="lbl">排列</div><div className="seg">{mode('stack', '上下', STACK_SVG)}{mode('side', '左右', SIDE_SVG)}</div></div>
        <div><div className="lbl">显示</div><div className="checks">{check('showExplain', '讲解')}{check('showNotes', '笔记')}</div></div>
        <p className="tip">拖动区域之间的分隔线可以调整大小，双击分隔线恢复这一处的默认比例。</p>
        <button className="btn" onClick={() => onChange(structuredClone(DEFAULT_LAYOUT))}>恢复默认布局</button>
      </div>
    </details>
  )
}
