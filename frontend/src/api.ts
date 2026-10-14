// Backend API client. Endpoints: DESIGN.md §7.2.

export type Rect = [number, number, number, number] // x, y, w, h — 0–1 relative

export interface Message {
  id: number
  thread_id: string
  role: 'user' | 'assistant'
  content: string
  created_at: string
}

export interface Thread {
  id: string
  page: number
  n: number
  rect: Rect
  minimized: boolean
  created_at: string
  messages: Message[]
}

export interface JobInfo {
  id: string
  kind: 'explain' | 'answer'
  status: 'queued' | 'running' | 'done' | 'error'
  page?: number
  thread_id?: string
  message_id?: number
}

export interface Explanation {
  content: string
  model: string | null
  created_at: string
}

export interface TreeTopic {
  title: string
  summary?: string
  slides: number[]
}

export interface Course {
  id: string
  title: string
  path: string
  slide_count: number
  tree: { title: string; children: TreeTopic[] } | null
  question_counts: Record<string, number>
  pages_with_notes: number[]
}

export interface SlideData {
  page: number
  text: string
  text_blocks: { x: number; y: number; w: number; h: number; text: string }[]
  explanation: Explanation | null
  explanation_job: JobInfo | null
  threads: Thread[]
  answer_jobs: JobInfo[]
  note: string
}

export interface RecentItem {
  path: string
  title: string
  opened_at: string
  exists: boolean
}

export type JobEvent =
  | { type: 'queued' }
  | { type: 'start' }
  | { type: 'delta'; text: string }
  | { type: 'reset' }
  | { type: 'done'; message?: Message; explanation?: Explanation & { page: number } }
  | { type: 'error'; message: string }

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function req<T>(method: string, url: string, body?: unknown): Promise<T> {
  const init: RequestInit = { method }
  if (body instanceof FormData) init.body = body
  else if (body !== undefined) {
    init.body = JSON.stringify(body)
    init.headers = { 'Content-Type': 'application/json' }
  }
  const r = await fetch(url, init)
  if (!r.ok) {
    let detail = r.statusText
    try {
      const j = await r.json()
      detail = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail)
    } catch {
      /* not json */
    }
    throw new ApiError(r.status, detail)
  }
  return r.json() as Promise<T>
}

const c = (cid: string) => `/api/courses/${encodeURIComponent(cid)}`

export const api = {
  health: () => req<{ provider: string; model: string; claude_found: boolean }>('GET', '/api/health'),
  recent: () => req<RecentItem[]>('GET', '/api/recent'),
  importPdf: (file: File) => {
    const fd = new FormData()
    fd.append('file', file)
    return req<Course>('POST', '/api/courses/import', fd)
  },
  open: (path: string) => req<Course>('POST', '/api/courses/open', { path }),
  course: (cid: string) => req<Course>('GET', c(cid)),
  slide: (cid: string, page: number) => req<SlideData>('GET', `${c(cid)}/slides/${page}`),
  imageUrl: (cid: string, page: number) => `${c(cid)}/slides/${page}/image`,
  cropUrl: (cid: string, tid: string) => `${c(cid)}/threads/${tid}/crop`,
  explain: (cid: string, page: number, regenerate = false) =>
    req<{ explanation?: Explanation; job?: JobInfo }>(
      'POST', `${c(cid)}/slides/${page}/explanation${regenerate ? '?regenerate=true' : ''}`),
  ask: (cid: string, page: number, rect: Rect, question: string) =>
    req<{ thread: Thread; job: JobInfo }>('POST', `${c(cid)}/slides/${page}/threads`, { rect, question }),
  followUp: (cid: string, tid: string, text: string) =>
    req<{ message: Message; job: JobInfo }>('POST', `${c(cid)}/threads/${tid}/messages`, { text }),
  retry: (cid: string, tid: string) => req<{ job: JobInfo }>('POST', `${c(cid)}/threads/${tid}/retry`),
  setMinimized: (cid: string, tid: string, minimized: boolean) =>
    req<{ ok: true }>('PATCH', `${c(cid)}/threads/${tid}`, { minimized }),
  deleteThread: (cid: string, tid: string) => req<{ ok: true }>('DELETE', `${c(cid)}/threads/${tid}`),
  putNote: (cid: string, page: number, content: string) =>
    req<{ ok: true }>('PUT', `${c(cid)}/slides/${page}/note`, { content }),
  getUi: <T>(cid: string, key: string) => req<{ value: T | null }>('GET', `${c(cid)}/ui/${key}`),
  putUi: (cid: string, key: string, value: unknown) => req<{ ok: true }>('PUT', `${c(cid)}/ui/${key}`, { value }),
  session: (cid: string, page: number) =>
    req<{ session_id: string | null; messages: { role: string; thread: number; content: string }[] }>(
      'GET', `${c(cid)}/slides/${page}/session`),
}

/** Parse a text/event-stream body into data payloads. Exported for tests. */
export function* parseSse(buffer: string): Generator<{ data: string; rest: string }> {
  let rest = buffer
  let i: number
  while ((i = rest.indexOf('\n\n')) !== -1) {
    const block = rest.slice(0, i)
    rest = rest.slice(i + 2)
    const data = block
      .split('\n')
      .filter((l) => l.startsWith('data:'))
      .map((l) => l.slice(5).replace(/^ /, ''))
      .join('\n')
    yield { data, rest }
  }
}

/**
 * Follow a job's events. The server replays from the start on every subscribe,
 * and we do NOT auto-reconnect (that would replay twice). Resolves when the job ends.
 */
export async function followJob(jobId: string, onEvent: (e: JobEvent) => void, signal?: AbortSignal) {
  const r = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/events`, { signal })
  if (!r.ok || !r.body) throw new ApiError(r.status, 'job stream unavailable')
  const reader = r.body.pipeThrough(new TextDecoderStream()).getReader()
  let buf = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buf += value
    let rest = buf
    for (const ev of parseSse(buf)) {
      rest = ev.rest
      if (ev.data) onEvent(JSON.parse(ev.data) as JobEvent)
    }
    buf = rest
  }
}
