import { useCallback, useEffect, useRef, useState } from 'react'
import { api, followJob, type Course, type JobInfo } from './api'

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
          title="上传老师讲课的文字稿（.txt / .vtt / .srt），讲解和问答会参考对应的段落">上传 transcript</button>
        {input}
      </>
    )
  }
  return (
    <details className="menu" ref={det}>
      <summary className="btn ghost" style={{ listStyle: 'none' }}>
        Transcript · {course.transcript.aligned_pages.length}/{course.slide_count} 页
      </summary>
      <div className="menu-pop">
        <p className="tip">{course.transcript.name}：已对齐到 {course.transcript.aligned_pages.length} 页。之后生成的讲解和新的问答会参考对应的段落；已有的讲解可以在讲解面板里"重新生成"。</p>
        <button className="btn" disabled={busy} onClick={() => { close(); t.realign() }}>重新对齐</button>
        <button className="btn" disabled={busy} onClick={() => { close(); file.current?.click() }}>换一个文件</button>
        <button className="btn" disabled={busy}
          onClick={() => { close(); if (confirm('删除 transcript 和所有对齐结果？已生成的讲解不受影响。')) t.remove() }}>删除</button>
      </div>
      {input}
    </details>
  )
}

export function TranscriptBanner({ t }: { t: ReturnType<typeof useTranscript> }) {
  if (t.st.status === 'aligning') return <p className="warn">Claude 正在把 transcript 对齐到每一页…</p>
  if (t.st.status === 'error') return <p className="merr">transcript 对齐失败：{t.st.error}
    <button className="btn" onClick={t.realign}>重试</button></p>
  if (t.st.status === 'done') return <p className="warn">transcript 已对齐到 {t.st.pages} 页。</p>
  return null
}
