import { forwardRef, useRef } from 'react'
import type { Thread } from './api'
import { clamp, dragRect, MIN_SELECTION } from './geometry'

export interface Sel {
  x: number
  y: number
  w: number
  h: number
}

const pct = (r: Sel) => ({ left: `${r.x * 100}%`, top: `${r.y * 100}%`, width: `${r.w * 100}%`, height: `${r.h * 100}%` })
export const asSel = (t: Thread): Sel => ({ x: t.rect[0], y: t.rect[1], w: t.rect[2], h: t.rect[3] })
export const titleOf = (t: Thread) => t.messages.find((m) => m.role === 'user')?.content ?? '新问题'

interface Props {
  src: string
  alt: string
  width: number | undefined
  threads: Thread[]
  busy: Set<string>
  sel: Sel | null
  dragging: boolean
  onSelect: (sel: Sel | null, dragging: boolean) => void
  onOpenThread: (tid: string) => void
  onImageLoad: (aspect: number) => void
}

/** Slide image + overlay: drag to select a region, markers for minimized questions. */
export const Stage = forwardRef<HTMLDivElement, Props>(function Stage(
  { src, alt, width, threads, busy, sel, dragging, onSelect, onOpenThread, onImageLoad }, ref,
) {
  const overlay = useRef<HTMLDivElement>(null)
  const drag = useRef<{ a: { x: number; y: number }; id: number } | null>(null)

  const rel = (e: React.PointerEvent) => {
    const r = overlay.current!.getBoundingClientRect()
    return { x: clamp((e.clientX - r.left) / r.width, 0, 1), y: clamp((e.clientY - r.top) / r.height, 0, 1) }
  }
  const down = (e: React.PointerEvent) => {
    if (e.target !== overlay.current || e.button !== 0) return
    e.preventDefault()
    const a = rel(e)
    drag.current = { a, id: e.pointerId }
    overlay.current!.setPointerCapture(e.pointerId)
    onSelect({ ...a, w: 0, h: 0 }, true)
  }
  const move = (e: React.PointerEvent) => {
    if (!drag.current || e.pointerId !== drag.current.id) return
    onSelect(dragRect(drag.current.a, rel(e)), true)
  }
  const up = (e: React.PointerEvent) => {
    if (!drag.current || e.pointerId !== drag.current.id) return
    const r = dragRect(drag.current.a, rel(e))
    drag.current = null
    onSelect(r.w < MIN_SELECTION || r.h < MIN_SELECTION ? null : r, false)
  }

  return (
    <div className="stage" ref={ref} style={width ? { width } : undefined}>
      <img src={src} alt={alt}
        onLoad={(e) => onImageLoad(e.currentTarget.naturalWidth / e.currentTarget.naturalHeight)} />
      <div className="overlay" ref={overlay} onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerCancel={up}>
        {threads.map((t) => {
          const pin = <span className={'pin' + (busy.has(t.id) ? ' busy' : '')}>{t.n}</span>
          return t.minimized ? (
            <button key={t.id} className="marker" style={pct(asSel(t))} title={titleOf(t)}
              aria-label={`打开问题 ${t.n}：${titleOf(t)}`} onClick={() => onOpenThread(t.id)}>{pin}</button>
          ) : (
            <div key={t.id} className="region" style={pct(asSel(t))}>{pin}</div>
          )
        })}
        {sel && (dragging || sel.w > 0) && <div className="selrect" style={pct(sel)} />}
      </div>
    </div>
  )
})
