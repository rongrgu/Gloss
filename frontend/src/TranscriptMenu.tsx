import { useCallback, useEffect, useRef, useState } from 'react'
import { api, followJob, type Course, type JobInfo } from './api'
import { useT } from './i18n'

type St = { status: 'idle' } | { status: 'aligning' } | { status: 'error'; error: string } | { status: 'done'; pages: number }

/**
 * Course-level transcript control (DESIGN.md §5.3): upload / re-align / replace / delete.
 * Alignment is one Claude call; the result shows up as `transcript_span` on each slide.
 */
export function useTranscript(course: Course, onRefresh: () => void) {
  const [st, setSt] = useState<St>({ status: course.align_job ? 'aligning' : 'idle' })
  const ctrl = useRef<AbortController | null>(null)

  const follow = useCallback((job: JobInfo) => {
    ctrl.current?.abort()
    const c = new AbortController()
    ctrl.current = c
    setSt({ status: 'aligning' })
    followJob(job.id, (ev) => {
      if (ev.type === 'done') { setSt({ status: 'done', pages: ev.aligned_pages?.length ?? 0 }); onRefresh() }
      if (ev.type === 'error') setSt({ status: 'error', error: ev.message })
    }, c.signal).catch((e) => !c.signal.aborted && setSt({ status: 'error', error: String(e.message ?? e) }))
  }, [onRefresh])

  useEffect(() => {
    if (course.align_job) follow(course.align_job)
    return () => ctrl.current?.abort()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [course.id])

  const run = async (f: () => Promise<{ job: JobInfo }>) => {
    try { follow((await f()).job) } catch (e) { setSt({ status: 'error', error: e instanceof Error ? e.message : String(e) }) }
  }
  return {
    st,
    upload: (file: File) => run(() => api.uploadTranscript(course.id, file)),
    realign: () => run(() => api.realignTranscript(course.id)),
    remove: async () => { await api.deleteTranscript(course.id); setSt({ status: 'idle' }); onRefresh() },
  }
}

export function TranscriptMenu({ course, t }: { course: Course; t: ReturnType<typeof useTranscript> }) {
  const m = useT()
  const file = useRef<HTMLInputElement>(null)
  const det = useRef<HTMLDetailsElement>(null)
  const close = () => { if (det.current) det.current.open = false }
  const input = (
    <input ref={file} type="file" hidden accept=".txt,.md,.vtt,.srt,text/plain,text/vtt"
      onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ''; if (f) t.upload(f) }} />
  )
  const busy = t.st.status === 'aligning'
  if (!course.transcript) {
    return (
      <>
        <button className="btn ghost" disabled={busy} onClick={() => file.current?.click()}
          title={m.uploadTranscriptTitle}>{m.uploadTranscript}</button>
        {input}
      </>
    )
  }
  return (
    <details className="menu" ref={det}>
      <summary className="btn ghost" style={{ listStyle: 'none' }}>
        {m.transcriptPages(course.transcript.aligned_pages.length, course.slide_count)}
      </summary>
      <div className="menu-pop">
        <p className="tip">{m.transcriptInfo(course.transcript.name, course.transcript.aligned_pages.length)}</p>
        <button className="btn" disabled={busy} onClick={() => { close(); t.realign() }}>{m.realign}</button>
        <button className="btn" disabled={busy} onClick={() => { close(); file.current?.click() }}>{m.replaceFile}</button>
        <button className="btn" disabled={busy}
          onClick={() => { close(); if (confirm(m.deleteTranscriptConfirm)) t.remove() }}>{m.delete}</button>
      </div>
      {input}
    </details>
  )
}

export function TranscriptBanner({ t }: { t: ReturnType<typeof useTranscript> }) {
  const m = useT()
  if (t.st.status === 'aligning') return <p className="warn">{m.aligning}</p>
  if (t.st.status === 'error') return <p className="merr">{m.alignFailed}{t.st.error}
    <button className="btn" onClick={t.realign}>{m.retry}</button></p>
  if (t.st.status === 'done') return <p className="warn">{m.aligned(t.st.pages)}</p>
  return null
}
