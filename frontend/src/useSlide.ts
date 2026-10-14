import { useCallback, useEffect, useRef, useState } from 'react'
import { api, followJob, type Explanation, type JobEvent, type JobInfo, type Rect, type SlideData, type Thread } from './api'

/** Live state of one answer being generated for a thread. */
export interface AnswerStream {
  jobId: string
  status: 'queued' | 'answering' | 'error'
  text: string
  error?: string
}

export type ExplainState =
  | { status: 'none' }
  | { status: 'waiting' } // will auto-generate if the user stays on the page
  | { status: 'queued' | 'running'; text: string }
  | { status: 'done'; explanation: Explanation }
  | { status: 'error'; error: string }

const AUTO_EXPLAIN_DELAY = 1200 // ms on a page before its explanation is generated

/**
 * Everything about one slide: its data, threads with streaming answers, and the explanation.
 * Jobs keep running on the server when we navigate away; on return, `answer_jobs` /
 * `explanation_job` from the slide endpoint let us pick their streams back up.
 */
export function useSlide(cid: string, page: number, onCourseChanged: () => void) {
  const [slide, setSlide] = useState<SlideData | null>(null)
  const [threads, setThreads] = useState<Thread[]>([])
  const [streams, setStreams] = useState<Record<string, AnswerStream>>({})
  const [explain, setExplain] = useState<ExplainState>({ status: 'none' })
  const [loadError, setLoadError] = useState<string | null>(null)
  const abort = useRef<AbortController | null>(null)
  const changed = useRef(onCourseChanged)
  useEffect(() => { changed.current = onCourseChanged }, [onCourseChanged])

  const followAnswer = useCallback((job: JobInfo, tid: string) => {
    const signal = abort.current?.signal
    setStreams((s) => ({ ...s, [tid]: { jobId: job.id, status: job.status === 'running' ? 'answering' : 'queued', text: '' } }))
    const on = (ev: JobEvent) => {
      if (ev.type === 'done' && ev.message) {
        const m = ev.message
        setThreads((ts) => ts.map((t) => (t.id === tid && !t.messages.some((x) => x.id === m.id)
          ? { ...t, messages: [...t.messages, m] } : t)))
        setStreams(({ [tid]: _, ...rest }) => rest)
        return
      }
      setStreams((s) => {
        const cur = s[tid]
        if (!cur || cur.jobId !== job.id) return s
        switch (ev.type) {
          case 'queued': return { ...s, [tid]: { ...cur, status: 'queued' } }
          case 'start': return { ...s, [tid]: { ...cur, status: 'answering' } }
          case 'delta': return { ...s, [tid]: { ...cur, status: 'answering', text: cur.text + ev.text } }
          case 'reset': return { ...s, [tid]: { ...cur, text: '' } }
          case 'error': return { ...s, [tid]: { ...cur, status: 'error', error: ev.message } }
          default: return s
        }
      })
    }
    followJob(job.id, on, signal).catch((e) => {
      if (signal?.aborted) return
      setStreams((s) => (s[tid]?.jobId === job.id
        ? { ...s, [tid]: { ...s[tid], status: 'error', error: String(e.message ?? e) } } : s))
    })
  }, [])

  const followExplain = useCallback((job: JobInfo) => {
    const signal = abort.current?.signal
    setExplain({ status: job.status === 'running' ? 'running' : 'queued', text: '' })
    followJob(job.id, (ev) => {
      setExplain((cur) => {
        const text = 'text' in cur ? cur.text : ''
        switch (ev.type) {
          case 'start': return { status: 'running', text }
          case 'delta': return { status: 'running', text: text + ev.text }
          case 'reset': return { status: 'running', text: '' }
          case 'done': return ev.explanation ? { status: 'done', explanation: ev.explanation } : cur
          case 'error': return { status: 'error', error: ev.message }
          default: return cur
        }
      })
    }, signal).catch((e) => !signal?.aborted && setExplain({ status: 'error', error: String(e.message ?? e) }))
  }, [])

  const generate = useCallback(async (regenerate = false) => {
    try {
      const r = await api.explain(cid, page, regenerate)
      if (r.explanation) setExplain({ status: 'done', explanation: r.explanation })
      else if (r.job) followExplain(r.job)
    } catch (e) {
      setExplain({ status: 'error', error: e instanceof Error ? e.message : String(e) })
    }
  }, [cid, page, followExplain])

  // Load the slide, resume running jobs, schedule the explanation.
  useEffect(() => {
    const ctrl = new AbortController()
    abort.current = ctrl
    setSlide(null)
    setThreads([])
    setStreams({})
    setExplain({ status: 'none' })
    setLoadError(null)
    let timer: number | undefined
    api.slide(cid, page).then((s) => {
      if (ctrl.signal.aborted) return
      setSlide(s)
      setThreads(s.threads)
      for (const j of s.answer_jobs) if (j.thread_id) followAnswer(j, j.thread_id)
      if (s.explanation) setExplain({ status: 'done', explanation: s.explanation })
      else if (s.explanation_job) followExplain(s.explanation_job)
      else {
        setExplain({ status: 'waiting' })
        timer = window.setTimeout(() => !ctrl.signal.aborted && generate(), AUTO_EXPLAIN_DELAY)
      }
    }).catch((e) => !ctrl.signal.aborted && setLoadError(e instanceof Error ? e.message : String(e)))
    return () => {
      ctrl.abort()
      clearTimeout(timer)
    }
  }, [cid, page, followAnswer, followExplain, generate])

  const ask = useCallback(async (rect: Rect, question: string) => {
    const r = await api.ask(cid, page, rect, question)
    setThreads((ts) => [...ts, r.thread])
    followAnswer(r.job, r.thread.id)
    changed.current()
    return r.thread
  }, [cid, page, followAnswer])

  const followUp = useCallback(async (tid: string, text: string) => {
    const r = await api.followUp(cid, tid, text)
    setThreads((ts) => ts.map((t) => (t.id === tid ? { ...t, messages: [...t.messages, r.message] } : t)))
    followAnswer(r.job, tid)
  }, [cid, followAnswer])

  const retry = useCallback(async (tid: string) => {
    const r = await api.retry(cid, tid)
    followAnswer(r.job, tid)
  }, [cid, followAnswer])

  const setMinimized = useCallback((tid: string, minimized: boolean) => {
    setThreads((ts) => ts.map((t) => (t.id === tid ? { ...t, minimized } : t)))
    api.setMinimized(cid, tid, minimized).catch(() => {})
  }, [cid])

  const remove = useCallback(async (tid: string) => {
    await api.deleteThread(cid, tid)
    setThreads((ts) => ts.filter((t) => t.id !== tid))
    changed.current()
  }, [cid])

  return { slide, threads, streams, explain, loadError, generate, ask, followUp, retry, setMinimized, remove }
}
