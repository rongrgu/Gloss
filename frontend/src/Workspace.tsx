import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, type Course, type Message } from './api'
import { Drawer } from './Drawer'
import { ChatBox, Composer } from './Floats'
import {
  appendNote, clamp, DEFAULT_LAYOUT, fitWidth, gridFor, normalizeLayout, noteClip, SPLIT_MAX, SPLIT_MIN, type Layout,
} from './geometry'
import { Icon } from './Icon'
import { LayoutMenu } from './LayoutMenu'
import { Markdown } from './Markdown'
import { go } from './route'
import { Stage, type Sel } from './Stage'
import { useToast } from './toast'
import { useSlide, type ExplainState } from './useSlide'

const isNarrow = () => window.matchMedia('(max-width:760px)').matches

export default function Workspace({ course, page, onCourseChanged }: {
  course: Course
  page: number
  onCourseChanged: () => void
}) {
  const cid = course.id
  const pageCount = course.slide_count
  const valid = page >= 1 && page <= pageCount
  useEffect(() => {
    if (!valid) go({ name: 'slide', cid, page: clamp(page || 1, 1, pageCount) }, true)
  }, [valid, cid, page, pageCount])

  const s = useSlide(cid, valid ? page : 1, onCourseChanged)
  const [layout, setLayoutState] = useState<Layout>(DEFAULT_LAYOUT)
  const [sel, setSel] = useState<Sel | null>(null)
  const [dragging, setDragging] = useState(false)
  const [drawer, setDrawer] = useState(false)
  const [z, setZ] = useState<{ top: number; of: Record<string, number> }>({ top: 10, of: {} })
  const [tick, setTick] = useState(0)
  const [aspect, setAspect] = useState(16 / 9)
  const [stageW, setStageW] = useState<number>()
  const [toast, showToast] = useToast()
  const note = useNote(cid, page, s.slide?.note)

  const wsRef = useRef<HTMLDivElement>(null)
  const groupRef = useRef<HTMLDivElement>(null)
  const paneRef = useRef<HTMLElement>(null)
  const stageRef = useRef<HTMLDivElement>(null)
  const notesPane = useRef<HTMLElement>(null)
  const bump = useCallback(() => setTick((n) => n + 1), [])

  // --- layout: load once per course, save on change ---
  useEffect(() => {
    api.getUi<Layout>(cid, 'layout').then((r) => setLayoutState(normalizeLayout(r.value))).catch(() => {})
  }, [cid])
  const saveTimer = useRef<number>(undefined)
  const setLayout = useCallback((l: Layout, persist = true) => {
    setLayoutState(l)
    if (persist) {
      clearTimeout(saveTimer.current)
      saveTimer.current = window.setTimeout(() => api.putUi(cid, 'layout', l).catch(() => {}), 300)
    }
  }, [cid])
  useEffect(() => { if (valid) api.putUi(cid, 'page', page).catch(() => {}) }, [cid, page, valid])

  // --- fit the slide into its pane; re-place floating boxes on any geometry change ---
  useEffect(() => {
    const fit = () => {
      const p = paneRef.current
      if (!p) return
      setStageW(isNarrow() ? undefined : fitWidth(p.clientWidth, p.clientHeight, aspect))
      bump()
    }
    fit()
    const ro = new ResizeObserver(fit)
    if (paneRef.current) ro.observe(paneRef.current)
    window.addEventListener('resize', fit)
    const onScroll = () => isNarrow() && bump()
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => { ro.disconnect(); window.removeEventListener('resize', fit); window.removeEventListener('scroll', onScroll) }
  }, [aspect, layout, bump])
  useEffect(bump, [stageW, bump])

  // --- page change resets transient UI ---
  useEffect(() => { setSel(null); setDragging(false) }, [page])

  const goPage = useCallback((p: number) => {
    if (p >= 1 && p <= pageCount) go({ name: 'slide', cid, page: p })
  }, [cid, pageCount])

  const front = (tid: string) => setZ((cur) => ({ top: cur.top + 1, of: { ...cur.of, [tid]: cur.top + 1 } }))

  // --- keyboard (mockup: ← → page, Esc cancels selection / closes drawer) ---
  useEffect(() => {
    const on = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement
      const tag = (t.tagName || '').toLowerCase()
      if (tag === 'input' || tag === 'textarea' || t.getAttribute?.('role') === 'separator') return
      if (e.key === 'ArrowLeft') goPage(page - 1)
      if (e.key === 'ArrowRight') goPage(page + 1)
      if (e.key === 'Escape') { if (sel) setSel(null); else if (drawer) setDrawer(false) }
    }
    document.addEventListener('keydown', on)
    return () => document.removeEventListener('keydown', on)
  }, [page, goPage, sel, drawer])

  const grid = gridFor(layout)
  const busy = useMemo(() => new Set(Object.entries(s.streams).filter(([, v]) => v.status !== 'error').map(([k]) => k)),
    [s.streams])
  const answeringN = useMemo(() => {
    const tid = Object.entries(s.streams).find(([, v]) => v.status === 'answering')?.[0]
    return s.threads.find((t) => t.id === tid)?.n ?? null
  }, [s.streams, s.threads])
  const messageCount = s.threads.reduce((n, t) => n + t.messages.length, 0)

  const topic = course.tree?.children.find((c) => c.slides.includes(page))
  const pageTitle = s.slide?.text.split('\n').find((l) => l.trim())?.trim() || `第 ${page} 页`

  const clip = (tid: string, m: Message) => {
    const t = s.threads.find((x) => x.id === tid)!
    const q = [...t.messages].filter((x) => x.role === 'user' && x.id < m.id).pop()
    note.set(appendNote(note.value, noteClip(t.n, q?.content ?? '', m.content)), true)
    if (!layout.showNotes) setLayout({ ...layout, showNotes: true })
    const el = notesPane.current
    if (el) { el.classList.remove('flash'); void el.offsetWidth; el.classList.add('flash') }
    showToast(`已摘到第 ${page} 页的笔记`)
  }

  return (
    <div id="app" className="full">
      <header className="bar">
        <button className="btn ghost" onClick={() => go({ name: 'overview', cid })}><Icon name="back" />导图</button>
        <div className="crumb">
          <span className="sec">{topic?.title ?? course.title}</span>
          <span className="ttl">{pageTitle}</span>
        </div>
        <span className="spacer" />
        <div className="pager">
          <button className="btn ghost" aria-label="上一页" disabled={page <= 1} onClick={() => goPage(page - 1)}><Icon name="back" /></button>
          <span className="count">{page} / {pageCount}</span>
          <button className="btn ghost" aria-label="下一页" disabled={page >= pageCount} onClick={() => goPage(page + 1)}><Icon name="next" /></button>
        </div>
        <span className="vsep wide-only" />
        <LayoutMenu layout={layout} onChange={setLayout} />
        <button className="btn ghost" aria-pressed={drawer} onClick={() => setDrawer(!drawer)}><Icon name="log" />后台 session</button>
      </header>

      <div className="ws" ref={wsRef} style={grid.ws}>
        <section className="pane slide-pane" aria-label="Slide" ref={paneRef}>
          <p className="hint"><Icon name="select" />在 slide 上拖动框选任意区域，比如一个公式，就能针对它提问</p>
          {s.loadError ? <p className="err">{s.loadError}</p> : (
            <Stage ref={stageRef} src={api.imageUrl(cid, page)} alt={`第 ${page} 页 slide：${pageTitle}`}
              width={stageW} threads={s.threads} busy={busy} sel={sel} dragging={dragging}
              onSelect={(r, d) => { setSel(r); setDragging(d) }}
              onOpenThread={(tid) => { s.setMinimized(tid, false); front(tid) }}
              onImageLoad={setAspect} />
          )}
        </section>
        <Splitter which="a" axis={grid.splitAAxis} hidden={!grid.showGroup} layout={layout} setLayout={setLayout}
          container={wsRef} label="调整 slide 和下方区域的大小" />
        <div className="group" ref={groupRef} style={grid.group} hidden={!grid.showGroup}>
          <section className="pane panel" aria-label="讲解" hidden={!layout.showExplain}>
            <div className="phead">
              <h2>讲解</h2>
              <span className="meta">{s.explain.status === 'done' && s.explain.explanation.model
                ? `由 ${s.explain.explanation.model} 生成 · 没有上传 transcript，讲解只基于 slide 内容`
                : '没有上传 transcript，讲解只基于 slide 内容'}</span>
              {s.explain.status === 'done' && (
                <span className="acts"><button className="linkbtn" onClick={() => s.generate(true)}><Icon name="refresh" />重新生成</button></span>
              )}
            </div>
            <div className="pbody"><div className="explain-body"><ExplainBody st={s.explain} onGenerate={() => s.generate()} /></div></div>
          </section>
          <Splitter which="b" axis={grid.splitBAxis} hidden={!grid.showSplitB} layout={layout} setLayout={setLayout}
            container={groupRef} label="调整讲解和笔记的大小" />
          <section className="pane panel" aria-label="笔记" hidden={!layout.showNotes} ref={notesPane}>
            <div className="phead"><h2>笔记</h2><span className="meta">{note.status}</span></div>
            <textarea className="notes" aria-label={`第 ${page} 页的笔记`} value={note.value}
              placeholder="这一页的笔记。可以自己写，也可以从问答里把回答摘过来。"
              disabled={!s.slide} onChange={(e) => note.set(e.target.value)} />
          </section>
        </div>
      </div>

      <div id="floats">
        {s.threads.filter((t) => !t.minimized).map((t) => (
          <ChatBox key={t.id} cid={cid} thread={t} stream={s.streams[t.id]} queuedBehind={answeringN}
            stage={stageRef} tick={tick} z={z.of[t.id] ?? 11} onFront={() => front(t.id)}
            onMinimize={() => s.setMinimized(t.id, true)}
            onDelete={() => s.remove(t.id).catch((e) => showToast(String(e.message ?? e)))}
            onFollowUp={(text) => s.followUp(t.id, text)} onRetry={() => s.retry(t.id)} onClip={(m) => clip(t.id, m)} />
        ))}
        {sel && !dragging && (
          <Composer key={`${sel.x},${sel.y}`} stage={stageRef} sel={sel} tick={tick} z={z.top + 1}
            onCancel={() => setSel(null)}
            onSubmit={async (q) => {
              const t = await s.ask([sel.x, sel.y, sel.w, sel.h].map((v) => +v.toFixed(4)) as [number, number, number, number], q)
              setSel(null)
              front(t.id)
            }} />
        )}
      </div>
      <Drawer cid={cid} page={page} open={drawer} version={messageCount} onClose={() => setDrawer(false)} />
      {toast}
    </div>
  )
}

function ExplainBody({ st, onGenerate }: { st: ExplainState; onGenerate: () => void }) {
  switch (st.status) {
    case 'done':
      return <Markdown text={st.explanation.content} />
    case 'queued':
      return <p className="loading">排队中，马上开始生成讲解…</p>
    case 'running':
      return st.text ? <Markdown className="streaming" text={st.text} /> : <p className="loading">Claude 正在生成这一页的讲解…</p>
    case 'error':
      return <div className="merr" role="alert"><span>讲解生成失败：{st.error}</span><button className="btn" onClick={onGenerate}>重试</button></div>
    default:
      return (
        <div className="empty">
          <span>这一页的讲解还没有生成。讲解在第一次打开时生成，之后保存在文件里。</span>
          <button className="btn primary" onClick={onGenerate}>生成讲解</button>
        </div>
      )
  }
}

/** Draggable divider between panes (mockup bindSplit): drag, double-click to reset, arrow keys. */
function Splitter({ which, axis, hidden, layout, setLayout, container, label }: {
  which: 'a' | 'b'
  axis: 'h' | 'v'
  hidden: boolean
  layout: Layout
  setLayout: (l: Layout, persist?: boolean) => void
  container: React.RefObject<HTMLDivElement | null>
  label: string
}) {
  const [drag, setDrag] = useState(false)
  const value = layout[layout.mode][which]
  const set = (v: number, persist: boolean) =>
    setLayout({ ...layout, [layout.mode]: { ...layout[layout.mode], [which]: clamp(v, SPLIT_MIN, SPLIT_MAX) } }, persist)
  const fromEvent = (e: React.PointerEvent) => {
    const box = container.current!.getBoundingClientRect()
    return axis === 'h' ? (e.clientY - box.top) / box.height : (e.clientX - box.left) / box.width
  }
  return (
    <div className={`split ${axis}${drag ? ' drag' : ''}`} hidden={hidden} tabIndex={0} role="separator" aria-label={label}
      aria-orientation={axis === 'h' ? 'horizontal' : 'vertical'} aria-valuenow={Math.round(value * 100)}
      aria-valuemin={SPLIT_MIN * 100} aria-valuemax={SPLIT_MAX * 100}
      onPointerDown={(e) => { e.preventDefault(); e.currentTarget.setPointerCapture(e.pointerId); setDrag(true) }}
      onPointerMove={(e) => drag && set(fromEvent(e), false)}
      onPointerUp={(e) => { if (drag) { setDrag(false); set(fromEvent(e), true) } }}
      onPointerCancel={() => { setDrag(false); setLayout(layout, true) }}
      onDoubleClick={() => set(DEFAULT_LAYOUT[layout.mode][which], true)}
      onKeyDown={(e) => {
        const dec = axis === 'h' ? 'ArrowUp' : 'ArrowLeft'
        const inc = axis === 'h' ? 'ArrowDown' : 'ArrowRight'
        if (e.key === dec) { e.preventDefault(); set(value - 0.03, true) }
        if (e.key === inc) { e.preventDefault(); set(value + 0.03, true) }
      }} />
  )
}

/** One page's note: debounced autosave, flushed when the page changes or the app closes. */
function useNote(cid: string, page: number, initial: string | undefined) {
  const [value, setValue] = useState('')
  const [status, setStatus] = useState('')
  const pending = useRef<{ page: number; content: string } | null>(null)
  const timer = useRef<number>(undefined)

  const flush = useCallback(() => {
    clearTimeout(timer.current)
    const p = pending.current
    if (!p) return
    pending.current = null
    api.putNote(cid, p.page, p.content)
      .then(() => setStatus((st) => (pending.current ? st : '已保存')))
      .catch(() => setStatus('保存失败，请检查后台是否在运行'))
  }, [cid])

  useEffect(() => {
    flush()
    setValue(initial ?? '')
    setStatus(initial ? '已保存' : '')
  }, [page, initial, flush])
  useEffect(() => {
    window.addEventListener('beforeunload', flush)
    return () => { window.removeEventListener('beforeunload', flush); flush() }
  }, [flush])

  const set = useCallback((content: string, immediate = false) => {
    setValue(content)
    setStatus('正在保存…')
    pending.current = { page, content }
    clearTimeout(timer.current)
    if (immediate) flush()
    else timer.current = window.setTimeout(flush, 400)
  }, [page, flush])

  return { value, status, set }
}
