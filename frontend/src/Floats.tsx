import { useLayoutEffect, useRef, useState } from 'react'
import { api, type Message, type Thread } from './api'
import { placePopup, type Box } from './geometry'
import { useT } from './i18n'
import { Icon } from './Icon'
import { Markdown } from './Markdown'
import { titleOf, type Sel } from './Stage'
import { describeTool, type AnswerStream } from './useSlide'

const isNarrow = () => window.matchMedia('(max-width:760px)').matches

/** Where a popup points: `sel` (0–1) within `box` (viewport px). Read lazily so it follows layout changes. */
export type Anchor = () => { box: Box; sel: Sel } | null

export const stageAnchor = (stage: React.RefObject<HTMLElement | null>, sel: Sel): Anchor => () =>
  stage.current ? { box: stage.current.getBoundingClientRect(), sel } : null
export const elementAnchor = (el: React.RefObject<HTMLElement | null>): Anchor => () =>
  el.current ? { box: el.current.getBoundingClientRect(), sel: { x: 0, y: 0, w: 1, h: 1 } } : null
export const fixedAnchor = (box: Box): Anchor => () => ({ box, sel: { x: 0, y: 0, w: 1, h: 1 } })

/** A fixed-position box placed next to its anchor. Re-placed whenever `tick` changes or it resizes. */
function Popup({ anchor, width, tick, z, className, children, ...rest }: {
  anchor: Anchor
  width: number
  tick: number
  z: number
  className: string
  children: React.ReactNode
} & React.HTMLAttributes<HTMLDivElement>) {
  const el = useRef<HTMLDivElement>(null)
  const [, force] = useState(0)

  useLayoutEffect(() => {
    const box = el.current
    const a = anchor()
    if (!box || !a) return
    if (isNarrow()) {
      box.style.left = box.style.top = box.style.width = ''
      return
    }
    const bar = document.querySelector('.bar')?.getBoundingClientRect().bottom ?? 0
    const p = placePopup(a.box, a.sel, width, box.offsetHeight || 300,
      { width: innerWidth, height: innerHeight }, bar + 8)
    box.style.left = p.left + 'px'
    box.style.top = p.top + 'px'
    box.style.width = p.width + 'px'
  })
  useLayoutEffect(() => {
    // Height changes (streaming text) can push the box off-screen: re-place.
    const ro = new ResizeObserver(() => force((n) => n + 1))
    if (el.current) ro.observe(el.current)
    return () => ro.disconnect()
  }, [])
  void tick
  return <div ref={el} className={'pop ' + className} style={{ zIndex: z }} {...rest}>{children}</div>
}

export function Composer({ anchor, quote, tick, z, onCancel, onSubmit }: {
  anchor: Anchor
  quote?: string // asking about a passage of the explanation
  tick: number
  z: number
  onCancel: () => void
  onSubmit: (q: string) => Promise<void>
}) {
  const [q, setQ] = useState('')
  const m = useT()
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const ta = useRef<HTMLTextAreaElement>(null)
  useLayoutEffect(() => { ta.current?.focus() }, [])
  const submit = async () => {
    if (!q.trim() || busy) return
    setBusy(true)
    setErr(null)
    try {
      await onSubmit(q.trim())
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
      setBusy(false)
    }
  }
  return (
    <Popup anchor={anchor} width={320} tick={tick} z={z} className="composer">
      <label htmlFor="ask">{quote ? m.askQuote : m.askRegion}</label>
      {quote && <blockquote className="quote">{quote}</blockquote>}
      <textarea id="ask" ref={ta} value={q} placeholder={m.askPlaceholder}
        onChange={(e) => setQ(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); submit() }
          if (e.key === 'Escape') onCancel()
        }} />
      {err && <p className="err">{err}</p>}
      <div className="row">
        <button className="btn ghost" onClick={onCancel}>{m.cancel}</button>
        <button className="btn primary" onClick={submit} disabled={!q.trim() || busy}>{m.ask}</button>
      </div>
    </Popup>
  )
}

export function ChatBox({ cid, thread, stream, queuedBehind, anchor, tick, z, onFront, onMinimize, onDelete,
  onFollowUp, onRetry, onClip }: {
  cid: string
  thread: Thread
  stream: AnswerStream | undefined
  queuedBehind: number | null // number of the question the page session is busy with
  anchor: Anchor
  tick: number
  z: number
  onFront: () => void
  onMinimize: () => void
  onDelete: () => void
  onFollowUp: (text: string) => Promise<void>
  onRetry: () => void
  onClip: (m: Message) => void
}) {
  const [draft, setDraft] = useState('')
  const m = useT()
  const body = useRef<HTMLDivElement>(null)
  const nearBottom = useRef(true)
  const t = thread

  useLayoutEffect(() => {
    // Follow the stream only if the user hasn't scrolled up.
    const b = body.current
    if (b && nearBottom.current) b.scrollTop = b.scrollHeight
  }, [t.messages.length, stream?.text])

  const send = async () => {
    const text = draft.trim()
    if (!text || (stream && stream.status !== 'error')) return
    setDraft('')
    nearBottom.current = true
    try {
      await onFollowUp(text)
    } catch {
      setDraft(text)
    }
  }
  const busy = !!stream && stream.status !== 'error'
  const status = stream?.status === 'answering' && stream.activity ? describeTool(stream.activity, m)
    : stream?.status === 'answering' && !stream.text ? m.answering
    : stream?.status === 'queued' ? m.queued(queuedBehind)
    : ''

  return (
    <Popup anchor={anchor} width={360} tick={tick} z={z}
      className="cbox" role="dialog" aria-label={m.question(t.n)} onPointerDown={onFront}>
      <div className="chead">
        <span className="num">{t.n}</span>
        {t.rect ? (
          <img src={api.cropUrl(cid, t.id)} alt="" style={{ height: 36, maxWidth: 96, objectFit: 'contain', flex: 'none',
            borderRadius: 5, border: '1px solid var(--line)', background: '#fff' }} />
        ) : <span className="qsnip" title={t.quote ?? ''}>“{t.quote}”</span>}
        <span className="ttl">{titleOf(t, m.newQuestion)}</span>
        <button className="iconbtn" aria-label={m.deleteQuestion} title={m.delete} disabled={busy}
          onClick={() => confirm(m.deleteQuestionConfirm(t.n)) && onDelete()}><Icon name="trash" /></button>
        <button className="iconbtn" aria-label={m.minimizeQuestion} title={m.minimize} onClick={onMinimize}><Icon name="min" /></button>
      </div>
      <div className="cbody" ref={body}
        onScroll={(e) => { const b = e.currentTarget; nearBottom.current = b.scrollHeight - b.scrollTop - b.clientHeight < 90 }}>
        {t.messages.map((msg) => msg.role === 'user'
          ? <div key={msg.id} className="mq">{msg.content}</div>
          : (
            <div key={msg.id}>
              <Markdown className="ma" text={msg.content} />
              <div className="ma-actions">
                <button className="linkbtn" onClick={() => onClip(msg)}><Icon name="pen" />{m.clipToNotes}</button>
              </div>
            </div>
          ))}
        {stream?.text && <Markdown className="ma streaming" text={stream.text} />}
        {stream?.status === 'error' && (
          <div className="merr" role="alert">
            <span>{m.answerFailed}{stream.error}</span>
            <button className="btn" onClick={onRetry}>{m.retry}</button>
          </div>
        )}
      </div>
      <div className="status" aria-live="polite">{status}</div>
      <div className="cfoot">
        <input type="text" value={draft} placeholder={busy ? m.followUpWait : m.followUp}
          aria-label={m.followUpIn(t.n)} onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && !e.nativeEvent.isComposing) { e.preventDefault(); send() } }} />
        <button className="send" aria-label={m.sendFollowUp} onClick={send} disabled={busy || !draft.trim()}><Icon name="send" /></button>
      </div>
    </Popup>
  )
}
