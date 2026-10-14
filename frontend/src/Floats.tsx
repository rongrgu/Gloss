import { useLayoutEffect, useRef, useState } from 'react'
import { api, type Message, type Thread } from './api'
import { placePopup } from './geometry'
import { Icon } from './Icon'
import { Markdown } from './Markdown'
import { titleOf, type Sel } from './Stage'
import type { AnswerStream } from './useSlide'

const isNarrow = () => window.matchMedia('(max-width:760px)').matches

/** A fixed-position box placed next to `anchor` on the stage. Re-placed whenever `tick` changes or it resizes. */
function Popup({ stage, anchor, width, tick, z, className, children, ...rest }: {
  stage: React.RefObject<HTMLElement | null>
  anchor: Sel
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
    const st = stage.current
    if (!box || !st) return
    if (isNarrow()) {
      box.style.left = box.style.top = box.style.width = ''
      return
    }
    const bar = document.querySelector('.bar')?.getBoundingClientRect().bottom ?? 0
    const p = placePopup(st.getBoundingClientRect(), anchor, width, box.offsetHeight || 300,
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

export function Composer({ stage, sel, tick, z, onCancel, onSubmit }: {
  stage: React.RefObject<HTMLElement | null>
  sel: Sel
  tick: number
  z: number
  onCancel: () => void
  onSubmit: (q: string) => Promise<void>
}) {
  const [q, setQ] = useState('')
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
    <Popup stage={stage} anchor={sel} width={320} tick={tick} z={z} className="composer">
      <label htmlFor="ask">关于框选的这块，你想问什么？</label>
      <textarea id="ask" ref={ta} value={q} placeholder="比如：这个公式里的求和范围是什么意思？"
        onChange={(e) => setQ(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); submit() }
          if (e.key === 'Escape') onCancel()
        }} />
      {err && <p className="err">{err}</p>}
      <div className="row">
        <button className="btn ghost" onClick={onCancel}>取消</button>
        <button className="btn primary" onClick={submit} disabled={!q.trim() || busy}>提问</button>
      </div>
    </Popup>
  )
}

export function ChatBox({ cid, thread, stream, queuedBehind, stage, tick, z, onFront, onMinimize, onDelete,
  onFollowUp, onRetry, onClip }: {
  cid: string
  thread: Thread
  stream: AnswerStream | undefined
  queuedBehind: number | null // number of the question the page session is busy with
  stage: React.RefObject<HTMLElement | null>
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
  const status = stream?.status === 'answering' && !stream.text ? 'Claude 正在回答…'
    : stream?.status === 'queued'
      ? `排队中：这一页的 session 正在回答${queuedBehind ? `问题 ${queuedBehind}` : '另一个问题'}，同一个 session 一次只处理一条消息。`
      : ''

  return (
    <Popup stage={stage} anchor={{ x: t.rect[0], y: t.rect[1], w: t.rect[2], h: t.rect[3] }} width={360} tick={tick} z={z}
      className="cbox" role="dialog" aria-label={`问题 ${t.n}`} onPointerDown={onFront}>
      <div className="chead">
        <span className="num">{t.n}</span>
        <img src={api.cropUrl(cid, t.id)} alt="" style={{ height: 36, maxWidth: 96, objectFit: 'contain', flex: 'none',
          borderRadius: 5, border: '1px solid var(--line)', background: '#fff' }} />
        <span className="ttl">{titleOf(t)}</span>
        <button className="iconbtn" aria-label="删除这个问题" title="删除" disabled={busy}
          onClick={() => confirm(`删除问题 ${t.n} 和它的全部回答？`) && onDelete()}><Icon name="trash" /></button>
        <button className="iconbtn" aria-label="缩小这个问题" title="缩小" onClick={onMinimize}><Icon name="min" /></button>
      </div>
      <div className="cbody" ref={body}
        onScroll={(e) => { const b = e.currentTarget; nearBottom.current = b.scrollHeight - b.scrollTop - b.clientHeight < 90 }}>
        {t.messages.map((m) => m.role === 'user'
          ? <div key={m.id} className="mq">{m.content}</div>
          : (
            <div key={m.id}>
              <Markdown className="ma" text={m.content} />
              <div className="ma-actions">
                <button className="linkbtn" onClick={() => onClip(m)}><Icon name="pen" />摘到笔记</button>
              </div>
            </div>
          ))}
        {stream?.text && <Markdown className="ma streaming" text={stream.text} />}
        {stream?.status === 'error' && (
          <div className="merr" role="alert">
            <span>回答失败：{stream.error}</span>
            <button className="btn" onClick={onRetry}>重试</button>
          </div>
        )}
      </div>
      <div className="status" aria-live="polite">{status}</div>
      <div className="cfoot">
        <input type="text" value={draft} placeholder={busy ? '等这条回答完成后再追问…' : '继续追问…'}
          aria-label={`在问题 ${t.n} 里继续追问`} onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && !e.nativeEvent.isComposing) { e.preventDefault(); send() } }} />
        <button className="send" aria-label="发送追问" onClick={send} disabled={busy || !draft.trim()}><Icon name="send" /></button>
      </div>
    </Popup>
  )
}
