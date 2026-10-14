import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, type Course, type Message } from './api'
import { Drawer } from './Drawer'
import { ChatBox, Composer, elementAnchor, fixedAnchor, stageAnchor } from './Floats'
import {
  appendNote, clamp, DEFAULT_LAYOUT, fitWidth, gridFor, normalizeLayout, noteClip, SPLIT_MAX, SPLIT_MIN, type Box,
  type Layout,
} from './geometry'
import { LangSwitch, useT } from './i18n'
import { Icon } from './Icon'
import { LayoutMenu } from './LayoutMenu'
import { Markdown } from './Markdown'
import { go } from './route'
import { asSel, Stage, titleOf, type Sel } from './Stage'
import { useToast } from './toast'
import { useSlide, type ExplainState } from './useSlide'

const isNarrow = () => window.matchMedia('(max-width:760px)').matches

export default function Workspace({ course, page, onCourseChanged }: {
  course: Course
  page: number
  onCourseChanged: () => void
}) {
  const cid = course.id
  const m = useT()
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
  const [showSpan, setShowSpan] = useState(false)
  // Asking about a passage of the explanation: the current text selection, then the open composer.
  const [textSel, setTextSel] = useState<{ quote: string; box: Box } | null>(null)
  const [quoteAsk, setQuoteAsk] = useState<{ quote: string; box: Box } | null>(null)
  const explainPane = useRef<HTMLElement>(null)
  const explainBody = useRef<HTMLDivElement>(null)
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
  useEffect(() => { setSel(null); setDragging(false); setTextSel(null); setQuoteAsk(null) }, [page])
  useEffect(() => {
    const on = () => { const s = document.getSelection(); if (!s || s.isCollapsed) setTextSel(null) }
    document.addEventListener('selectionchange', on)
    return () => document.removeEventListener('selectionchange', on)
  }, [])
  const onExplainMouseUp = () => {
    const s = document.getSelection()
    if (!s || s.isCollapsed || !explainBody.current) return
    const range = s.getRangeAt(0)
    if (!explainBody.current.contains(range.commonAncestorContainer)) return
    const quote = s.toString().replace(/\n{3,}/g, '\n\n').trim()
    if (quote.length < 2) return
    const r = range.getBoundingClientRect()
    setTextSel({ quote: quote.slice(0, 2000), box: { left: r.left, top: r.top, width: r.width, height: r.height } })
  }

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
      if (e.key === 'Escape') { if (sel) setSel(null); else if (quoteAsk) setQuoteAsk(null); else if (drawer) setDrawer(false) }
    }
    document.addEventListener('keydown', on)
    return () => document.removeEventListener('keydown', on)
  }, [page, goPage, sel, drawer, quoteAsk])

  const grid = gridFor(layout)
  const busy = useMemo(() => new Set(Object.entries(s.streams).filter(([, v]) => v.status !== 'error').map(([k]) => k)),
    [s.streams])
  const answeringN = useMemo(() => {
    const tid = Object.entries(s.streams).find(([, v]) => v.status === 'answering')?.[0]
    return s.threads.find((t) => t.id === tid)?.n ?? null
  }, [s.streams, s.threads])
  const messageCount = s.threads.reduce((n, t) => n + t.messages.length, 0)

  const topic = course.tree?.children.find((c) => c.slides.includes(page))
  const pageTitle = s.slide?.text.split('\n').find((l) => l.trim())?.trim() || m.page(page)

  const clip = (tid: string, msg: Message) => {
    const t = s.threads.find((x) => x.id === tid)!
    const q = [...t.messages].filter((x) => x.role === 'user' && x.id < msg.id).pop()
    note.set(appendNote(note.value, noteClip(t.n, q?.content ?? '', msg.content, m)), true)
    if (!layout.showNotes) setLayout({ ...layout, showNotes: true })
    const el = notesPane.current
    if (el) { el.classList.remove('flash'); void el.offsetWidth; el.classList.add('flash') }
    showToast(m.clipped(page))
  }

  return (
    <div id="app" className="full">
      <header className="bar">
        <button className="btn ghost" onClick={() => go({ name: 'overview', cid })}><Icon name="back" />{m.map}</button>
        <div className="crumb">
          <span className="sec">{topic?.title ?? course.title}</span>
          <span className="ttl">{pageTitle}</span>
        </div>
        <span className="spacer" />
        <div className="pager">
          <button className="btn ghost" aria-label={m.prevPage} disabled={page <= 1} onClick={() => goPage(page - 1)}><Icon name="back" /></button>
          <span className="count">{page} / {pageCount}</span>
          <button className="btn ghost" aria-label={m.nextPage} disabled={page >= pageCount} onClick={() => goPage(page + 1)}><Icon name="next" /></button>
        </div>
        <span className="vsep wide-only" />
        <LayoutMenu layout={layout} onChange={setLayout} />
        <button className="btn ghost" aria-pressed={drawer} onClick={() => setDrawer(!drawer)}><Icon name="log" /><span className="wide-only">{m.sessionDrawer}</span></button>
        <LangSwitch />
      </header>

      <div className="ws" ref={wsRef} style={grid.ws}>
        <section className="pane slide-pane" aria-label="Slide" ref={paneRef}>
          <p className="hint"><Icon name="select" />{m.selectHint}</p>
          {s.loadError ? <p className="err">{s.loadError}</p> : (
            <Stage ref={stageRef} src={api.imageUrl(cid, page)} alt={m.slideAlt(page, pageTitle)}
              width={stageW} threads={s.threads} busy={busy} sel={sel} dragging={dragging}
              onSelect={(r, d) => { setSel(r); setDragging(d) }}
              onOpenThread={(tid) => { s.setMinimized(tid, false); front(tid) }}
              onImageLoad={setAspect} />
          )}
        </section>
        <Splitter which="a" axis={grid.splitAAxis} hidden={!grid.showGroup} layout={layout} setLayout={setLayout}
          container={wsRef} label={m.resizeSlide} />
        <div className="group" ref={groupRef} style={grid.group} hidden={!grid.showGroup}>
          <section className="pane panel" aria-label={m.explanation} hidden={!layout.showExplain} ref={explainPane}>
            <div className="phead">
              <h2>{m.explanation}</h2>
              <span className="meta">{[
                s.explain.status === 'done' && s.explain.explanation.model ? m.generatedBy(s.explain.explanation.model) : '',
                !course.transcript ? m.noTranscript
                  : s.slide?.transcript_span ? m.hasSpan : m.noSpan,
              ].filter(Boolean).join(' · ')}</span>
              <span className="acts">
                {s.slide?.transcript_span && (
                  <button className="linkbtn" aria-pressed={showSpan} onClick={() => setShowSpan(!showSpan)}>
                    {showSpan ? m.hideSpan : m.showSpan}</button>
                )}
                {s.explain.status === 'done' && (
                  <button className="linkbtn" onClick={() => s.generate(true)}><Icon name="refresh" />{m.regenerate}</button>
                )}
              </span>
            </div>
            <div className="pbody">
              {showSpan && s.slide?.transcript_span && <div className="span">{s.slide.transcript_span}</div>}
              <div className="explain-body" ref={explainBody} onMouseUp={onExplainMouseUp}>
                <ExplainBody st={s.explain} onGenerate={() => s.generate()} />
              </div>
              {s.threads.some((t) => t.quote) && (
                <div className="qchips" aria-label={m.explainQuestions}>
                  {s.threads.filter((t) => t.quote).map((t) => (
                    <button key={t.id} className="qchip" title={titleOf(t, m.newQuestion)}
                      onClick={() => { s.setMinimized(t.id, !t.minimized); front(t.id) }}>
                      <span className={'pin' + (busy.has(t.id) ? ' busy' : '')}>{t.n}</span>
                      <span className="qchip-t">{titleOf(t, m.newQuestion)}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>
          </section>
          <Splitter which="b" axis={grid.splitBAxis} hidden={!grid.showSplitB} layout={layout} setLayout={setLayout}
            container={groupRef} label={m.resizePanels} />
          <section className="pane panel" aria-label={m.notes} hidden={!layout.showNotes} ref={notesPane}>
            <div className="phead"><h2>{m.notes}</h2><span className="meta">{note.status && m[note.status]}</span></div>
            <textarea className="notes" aria-label={m.notesFor(page)} value={note.value}
              placeholder={m.notesPlaceholder}
              disabled={!s.slide} onChange={(e) => note.set(e.target.value)} />
          </section>
        </div>
      </div>

      <div id="floats">
        {s.threads.filter((t) => !t.minimized).map((t) => (
          <ChatBox key={t.id} cid={cid} thread={t} stream={s.streams[t.id]} queuedBehind={answeringN}
            anchor={t.rect ? stageAnchor(stageRef, asSel(t)) : elementAnchor(explainPane)}
            tick={tick} z={z.of[t.id] ?? 11} onFront={() => front(t.id)}
            onMinimize={() => s.setMinimized(t.id, true)}
            onDelete={() => s.remove(t.id).catch((e) => showToast(String(e.message ?? e)))}
            onFollowUp={(text) => s.followUp(t.id, text)} onRetry={() => s.retry(t.id)} onClip={(msg) => clip(t.id, msg)} />
        ))}
        {sel && !dragging && (
          <Composer key={`${sel.x},${sel.y}`} anchor={stageAnchor(stageRef, sel)} tick={tick} z={z.top + 1}
            onCancel={() => setSel(null)}
            onSubmit={async (q) => {
              const rect = [sel.x, sel.y, sel.w, sel.h].map((v) => +v.toFixed(4)) as [number, number, number, number]
              const t = await s.ask({ rect }, q)
              setSel(null)
              front(t.id)
            }} />
        )}
        {textSel && !quoteAsk && (
          <button className="btn primary askbtn" style={{ left: textSel.box.left + textSel.box.width / 2, top: textSel.box.top + textSel.box.height + 8 }}
            onMouseDown={(e) => e.preventDefault() /* keep the selection */}
            onClick={() => { setQuoteAsk(textSel); setTextSel(null); document.getSelection()?.removeAllRanges() }}>
            {m.askAboutThis}
          </button>
        )}
        {quoteAsk && (
          <Composer key={quoteAsk.quote} anchor={fixedAnchor(quoteAsk.box)} quote={quoteAsk.quote} tick={tick} z={z.top + 1}
            onCancel={() => setQuoteAsk(null)}
            onSubmit={async (q) => {
              const t = await s.ask({ quote: quoteAsk.quote }, q)
              setQuoteAsk(null)
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
  const m = useT()
  switch (st.status) {
    case 'done':
      return <Markdown text={st.explanation.content} />
    case 'queued':
      return <p className="loading">{m.explainQueued}</p>
    case 'running':
      return st.text ? <Markdown className="streaming" text={st.text} /> : <p className="loading">{m.explainRunning}</p>
    case 'error':
      return <div className="merr" role="alert"><span>{m.explainFailed}{st.error}</span><button className="btn" onClick={onGenerate}>{m.retry}</button></div>
    default:
      return (
        <div className="empty">
          <span>{m.explainNone}</span>
          <button className="btn primary" onClick={onGenerate}>{m.explainGenerate}</button>
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

type NoteStatus = '' | 'noteSaved' | 'noteSaving' | 'noteFailed' // keys into the dictionary

/** One page's note: debounced autosave, flushed when the page changes or the app closes. */
function useNote(cid: string, page: number, initial: string | undefined) {
  const [value, setValue] = useState('')
  const [status, setStatus] = useState<NoteStatus>('')
  const pending = useRef<{ page: number; content: string } | null>(null)
  const timer = useRef<number>(undefined)

  const flush = useCallback(() => {
    clearTimeout(timer.current)
    const p = pending.current
    if (!p) return
    pending.current = null
    api.putNote(cid, p.page, p.content)
      .then(() => setStatus((st) => (pending.current ? st : 'noteSaved')))
      .catch(() => setStatus('noteFailed'))
  }, [cid])

  useEffect(() => {
    flush()
    setValue(initial ?? '')
    setStatus(initial ? 'noteSaved' : '')
  }, [page, initial, flush])
  useEffect(() => {
    window.addEventListener('beforeunload', flush)
    return () => { window.removeEventListener('beforeunload', flush); flush() }
  }, [flush])

  const set = useCallback((content: string, immediate = false) => {
    setValue(content)
    setStatus('noteSaving')
    pending.current = { page, content }
    clearTimeout(timer.current)
    if (immediate) flush()
    else timer.current = window.setTimeout(flush, 400)
  }, [page, flush])

  return { value, status, set }
}
