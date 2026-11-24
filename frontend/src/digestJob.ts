import { useCallback, useEffect, useState } from 'react'
import { api, followJob, type Course, type Digest, type JobInfo } from './api'

/** Start or rejoin the lecture-summary job (§15.3); `onDone` gets the new summary. */
export function useDigestJob(course: Course, onDone: (d: Digest) => void) {
  const [running, setRunning] = useState(!!course.lecture.digest_job)
  const [error, setError] = useState<string | null>(null)

  const follow = useCallback((job: JobInfo, signal?: AbortSignal) => {
    setRunning(true)
    setError(null)
    followJob(job.id, (ev) => {
      if (ev.type === 'done') { setRunning(false); if (ev.digest) onDone(ev.digest) }
      if (ev.type === 'error') { setRunning(false); setError(ev.message) }
    }, signal).catch((e) => { if (!signal?.aborted) { setRunning(false); setError(String(e.message ?? e)) } })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [course.id])

  useEffect(() => {
    if (!course.lecture.digest_job) return
    const ctrl = new AbortController()
    follow(course.lecture.digest_job, ctrl.signal)
    return () => ctrl.abort()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [course.id])

  const start = useCallback(async () => {
    setError(null)
    try {
      follow((await api.makeDigest(course.id)).job)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }, [course.id, follow])

  return { running, error, start }
}
