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
  anchor_type: 'slide_region' | 'explanation_text'
  rect: Rect | null // slide_region
  quote: string | null // explanation_text
  minimized: boolean
  created_at: string
  understood: boolean | null // the student's own mark (§15.2): got it / still confused / not marked
  messages: Message[]
}

export interface JobInfo {
  id: string
  kind: 'explain' | 'answer' | 'tree' | 'align' | 'quiz' | 'digest' | 'profile' | 'cheatsheet'
  status: 'queued' | 'running' | 'done' | 'error'
  page?: number
  thread_id?: string
  message_id?: number
  sheet?: string // a cheatsheet revision (§16.4)
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

export interface Tree {
  title: string
  subtitle?: string
  children: TreeTopic[]
}

/** App-wide preferences (DESIGN.md §9.3). */
/** Which LLM answers (DESIGN.md §6.2). */
export interface ProviderInfo {
  provider: string | null // claude | codex | anthropic_api | openai_api; null: none installed
  provider_source: 'config' | 'choice' | 'auto' | 'pending' | 'none'
  available: string[] // installed CLIs
  needs_choice: boolean // both CLIs installed and none picked yet
  logged_in: boolean | null
}

export interface Prefs extends ProviderInfo {
  ui_language: 'zh' | 'en' | null // null: not chosen yet (English)
  language: string // Claude's explanation / answer language in effect
  language_pinned: boolean // set in config.toml; doesn't follow the interface
}

export interface Course {
  id: string
  title: string
  path: string
  slide_count: number
  aspect: number
  tree: Tree | null
  tree_job: JobInfo | null
  transcript: { name: string; aligned_pages: number[] } | null
  align_job: JobInfo | null
  question_counts: Record<string, number>
  pages_with_notes: number[]
  confused_counts: Record<string, number> // page -> questions marked "still confused"
  folder: string | null
  lecture: {
    status: 'new' | 'studying' | 'done'
    digest_at: string | null
    new_since_digest: number | null // messages since the latest summary
    digest_job: JobInfo | null
  }
}

/** A lecture's study summary (DESIGN.md §15.3). */
export interface Digest {
  content: string // Markdown with gloss: links
  up_to_message_id: number
  model: string | null
  created_at: string
  new_messages: number // library-relative folder, null if the file is outside the library
}

/** Library folder tree (DESIGN.md §13.2). Paths are library-relative with '/' separators. */
export type FolderKind = 'course' | 'group' | 'empty'

export interface Lecture {
  path: string
  file: string // absolute path, for api.open
  title: string
  slide_count: number
  questions: number
  course_id: string | null
  sort_key: number
  open: boolean
  error: string | null
  status: 'new' | 'studying' | 'done'
  new_since_digest: number | null
}

export interface Listing {
  path: string
  name: string
  root: string
  crumbs: { name: string; path: string }[]
  kind: FolderKind
  mixed: boolean
  folders: { path: string; name: string; kind: FolderKind; lectures: number; folders: number }[]
  lectures: Lecture[]
  has_quizzes: boolean
  has_study: boolean
}

/** The course's study profile: weak points by concept, with evidence (DESIGN.md §15.4). */
export type WeakStatus = 'open' | 'improving' | 'resolved'

export interface Evidence {
  id: number
  kind: 'thread' | 'quiz'
  lecture: string // course id of the .gloss
  page: number | null
  ref: string // thread id or quiz question id
  polarity: 1 | -1 // +1 not understood, -1 understood
  note: string
  created_at: string
}

export interface WeakPoint {
  id: string // W1, W2… never reused
  concept: string
  summary: string
  status: WeakStatus
  pinned: boolean // the student set the status; automatic updates leave it
  extra_evidence: number
  updated_at: string
  evidence: Evidence[]
}

export interface Profile {
  weak_points: WeakPoint[]
  pending: (Omit<Evidence, 'id'> & { id: number })[]
  revisions: { id: number; trigger: string; changes: Record<string, unknown>[]; undone: boolean; created_at: string }[]
  lectures: Record<string, { file: string; title: string; number: number }>
  job: JobInfo | null
}

/** Quizzes (DESIGN.md §13.5, §13.8). */
export type QuestionType = 'mcq' | 'short' | 'derivation'
export type QuizFocus = 'weak' | 'balanced' | 'uncovered'

export interface QuizSource {
  lecture: string | null // course id
  label: string // L1, L2… at generation time
  title: string
  course: string
  number: number
  page: number
  file: string | null // null: the lecture is no longer in this folder
}

export interface Attempt {
  answer: string
  correct: 0 | 1 | null
  created_at: string
}

export interface QuizQuestion {
  id: string
  n: number
  type: QuestionType
  stem: string
  options: string[]
  answer: string
  explanation: string
  why: string
  topic: string
  difficulty: number
  sources: QuizSource[]
  review: string | null // the answer check found a problem
  attempt: Attempt | null
}

export interface QuizSummary {
  id: string
  title: string
  created_at: string
  count: number
  answered: number
  correct: number
  options: {
    count: number; types: QuestionType[]; focus: QuizFocus; requested?: number | null
    mode?: QuizMode; exam?: { id: string; title: string } | null; instructions?: string
  }
  model: string | null
  folder?: string // where it is stored (deep listings on the library home, §17)
}

export interface Quiz {
  id: string
  title: string
  created_at: string
  model: string | null
  options: QuizSummary['options']
  questions: QuizQuestion[]
}

export type QuizMode = 'new' | 'replicate'

export interface QuizRequest {
  folder: string
  count: number
  types: QuestionType[]
  focus: QuizFocus
  lectures?: string[] | null
  mode?: QuizMode
  exam?: string | null // the past exam to replicate
  instructions?: string // the student's own requirements; win over count / types
}

/** The library home's catch-up (DESIGN.md §17). Times are ISO strings. */
export interface CatchUpLecture { title: string; file: string; folder: string; page: number | null; slides: number; questions: number; status: string; active_at: string }
export interface CatchUpData {
  last: CatchUpLecture | null
  continue: CatchUpLecture[]
  confused: { thread: string; page: number; question: string; lecture: string; file: string; folder: string; at: string }[]
  confused_total: number
  quizzes: { id: string; title: string; folder: string; count: number; answered: number; correct: number; created_at: string }[]
  weak_points: { concept: string; status: string; folder: string }[]
  weak_total: number
  sheet: { id: string; title: string; folder: string; updated_at: string } | null
}

/** Cheatsheets (DESIGN.md §16). `pages`: 0 = no limit. */
export interface SheetOptions { lectures: string[]; exams: string[]; pages: 0 | 1 | 2; instructions: string }
export interface SheetSummary {
  id: string; title: string; options: SheetOptions; created_at: string; updated_at: string; versions: number
  folder?: string // where it is stored, relative to the library (deep listings, §16.9)
}
export interface SheetVersion { id: number; source: 'generate' | 'edit' | 'ai' | 'restore' | 'rename'; note: string; created_at: string; chars: number }
export interface SheetMessage { id: number; role: 'user' | 'assistant'; content: string; version_id: number | null; created_at: string }
export interface Sheet extends Omit<SheetSummary, 'versions'> {
  content: string
  version: number
  versions: SheetVersion[]
  messages: SheetMessage[]
  job: JobInfo | null // a revision running for this sheet
}
export interface SheetRequest { folder: string; lectures: string[] | null; exams: string[]; pages: 0 | 1 | 2; instructions: string }

/** A past quiz / exam uploaded to a folder (§13.8). */
export interface PastExam {
  id: string
  title: string
  filename: string
  kind?: 'pdf' | 'docx'
  pages: number
  created_at: string
  folder: string // where it is stored, relative to the library
}

export interface SlideData {
  page: number
  text: string
  text_blocks: { x: number; y: number; w: number; h: number; text: string }[]
  transcript_span: string | null
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
  | { type: 'tool'; name: string; target: string } // 'page:7' | 'course' | 'search:<pattern>' | 'file' | 'files'
  | { type: 'done'; message?: Message; explanation?: Explanation & { page: number }; tree?: Tree;
      aligned_pages?: number[]; quiz_id?: string; count?: number; requested?: number | null; digest?: Digest
      sheet_id?: string; version?: number }
  | { type: 'phase'; phase: 'generate' | 'verify' | 'verify_failed' | 'profile' | 'profile_failed' | 'revise'; attempt?: number; message?: string }
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
  health: () => req<ProviderInfo & { model: string; version: string }>('GET', '/api/health'),
  recent: () => req<RecentItem[]>('GET', '/api/recent'),
  prefs: () => req<Prefs>('GET', '/api/prefs'),
  setPrefs: (ui_language: 'zh' | 'en') => req<Prefs>('PUT', '/api/prefs', { ui_language }),
  setProvider: (provider: string) => req<Prefs>('PUT', '/api/prefs', { provider }),
  importPdf: (file: File, folder = '') => {
    const fd = new FormData()
    fd.append('file', file)
    fd.append('folder', folder)
    return req<Course>('POST', '/api/courses/import', fd)
  },
  library: (path: string) => req<Listing>('GET', `/api/library?path=${encodeURIComponent(path)}`),
  libraryTree: () => req<{ path: string; depth: number; kind: FolderKind }[]>('GET', '/api/library/tree'),
  mkdir: (parent: string, name: string) => req<{ path: string }>('POST', '/api/library/folders', { parent, name }),
  rmdir: (path: string) => req<{ ok: true }>('DELETE', `/api/library/folders?path=${encodeURIComponent(path)}`),
  rename: (path: string, name: string) => req<{ path: string }>('POST', '/api/library/rename', { path, name }),
  move: (path: string, to: string) => req<{ path: string }>('POST', '/api/library/move', { path, to }),
  reorder: (path: string, before: string | null) => req<Listing>('POST', '/api/library/reorder', { path, before }),
  quizzes: (folder: string, deep = false) =>
    req<{ quizzes: QuizSummary[]; job: JobInfo | null }>('GET', `/api/library/quizzes?folder=${encodeURIComponent(folder)}${deep ? '&deep=true' : ''}`),
  exams: (folder: string) =>
    req<{ exams: PastExam[] }>('GET', `/api/library/exams?folder=${encodeURIComponent(folder)}`),
  uploadExam: (folder: string, file: File) => {
    const fd = new FormData()
    fd.append('file', file)
    fd.append('folder', folder)
    return req<PastExam>('POST', '/api/library/exams', fd)
  },
  examUrl: (e: PastExam) => `/api/library/exams/${e.id}/file?folder=${encodeURIComponent(e.folder)}`,
  renameExam: (e: PastExam, title: string) =>
    req<PastExam>('PATCH', `/api/library/exams/${e.id}?folder=${encodeURIComponent(e.folder)}`, { title }),
  deleteExam: (e: PastExam) =>
    req<{ ok: true }>('DELETE', `/api/library/exams/${e.id}?folder=${encodeURIComponent(e.folder)}`),
  catchup: () => req<CatchUpData>('GET', '/api/library/catchup'),
  sheets: (folder: string, deep = false) =>
    req<{ sheets: SheetSummary[]; job: JobInfo | null }>('GET', `/api/library/cheatsheets?folder=${encodeURIComponent(folder)}${deep ? '&deep=true' : ''}`),
  startSheet: (r: SheetRequest) => req<{ job: JobInfo }>('POST', '/api/library/cheatsheets', r),
  sheet: (folder: string, sid: string) =>
    req<Sheet>('GET', `/api/library/cheatsheets/${sid}?folder=${encodeURIComponent(folder)}`),
  saveSheet: (folder: string, sid: string, content: string, base: number) =>
    req<{ version: number; title: string }>('PUT', `/api/library/cheatsheets/${sid}?folder=${encodeURIComponent(folder)}`, { content, base }),
  reviseSheet: (folder: string, sid: string, request: string, selection: string) =>
    req<{ job: JobInfo }>('POST', `/api/library/cheatsheets/${sid}/revise?folder=${encodeURIComponent(folder)}`, { request, selection }),
  restoreSheet: (folder: string, sid: string, version: number) =>
    req<Sheet>('POST', `/api/library/cheatsheets/${sid}/restore?folder=${encodeURIComponent(folder)}`, { version }),
  sheetVersion: (folder: string, sid: string, vid: number) =>
    req<{ content: string }>('GET', `/api/library/cheatsheets/${sid}/versions/${vid}?folder=${encodeURIComponent(folder)}`),
  sheetDocxUrl: (folder: string, sid: string) => `/api/library/cheatsheets/${sid}/export.docx?folder=${encodeURIComponent(folder)}`,
  sheetMdUrl: (folder: string, sid: string) => `/api/library/cheatsheets/${sid}/export.md?folder=${encodeURIComponent(folder)}`,
  renameSheet: (folder: string, sid: string, title: string) =>
    req<Sheet>('PATCH', `/api/library/cheatsheets/${sid}?folder=${encodeURIComponent(folder)}`, { title }),
  deleteSheet: (folder: string, sid: string) =>
    req<{ ok: true }>('DELETE', `/api/library/cheatsheets/${sid}?folder=${encodeURIComponent(folder)}`),
  startQuiz: (r: QuizRequest) => req<{ job: JobInfo }>('POST', '/api/library/quizzes', r),
  quiz: (folder: string, qid: string) =>
    req<Quiz>('GET', `/api/library/quizzes/${qid}?folder=${encodeURIComponent(folder)}`),
  attempt: (folder: string, qid: string, question_id: string, answer: string, correct: boolean | null) =>
    req<Attempt>('POST', `/api/library/quizzes/${qid}/attempts?folder=${encodeURIComponent(folder)}`,
      { question_id, answer, correct }),
  renameQuiz: (folder: string, qid: string, title: string) =>
    req<{ id: string; title: string }>('PATCH', `/api/library/quizzes/${qid}?folder=${encodeURIComponent(folder)}`, { title }),
  deleteQuiz: (folder: string, qid: string) =>
    req<{ ok: true }>('DELETE', `/api/library/quizzes/${qid}?folder=${encodeURIComponent(folder)}`),
  uploadGloss: (file: File, folder = '') => {
    const fd = new FormData()
    fd.append('file', file)
    fd.append('folder', folder)
    return req<Course>('POST', '/api/courses/upload', fd)
  },
  open: (path: string) => req<Course>('POST', '/api/courses/open', { path }),
  course: (cid: string) => req<Course>('GET', c(cid)),
  buildTree: (cid: string, regenerate = false) =>
    req<{ tree?: Tree; job?: JobInfo }>('POST', `${c(cid)}/tree${regenerate ? '?regenerate=true' : ''}`),
  uploadTranscript: (cid: string, file: File) => {
    const fd = new FormData()
    fd.append('file', file)
    return req<{ job: JobInfo }>('POST', `${c(cid)}/transcript`, fd)
  },
  realignTranscript: (cid: string) => req<{ job: JobInfo }>('POST', `${c(cid)}/transcript/align`),
  deleteTranscript: (cid: string) => req<{ ok: true }>('DELETE', `${c(cid)}/transcript`),
  slide: (cid: string, page: number) => req<SlideData>('GET', `${c(cid)}/slides/${page}`),
  exportUrl: (cid: string) => `${c(cid)}/export.md`,
  imageUrl: (cid: string, page: number) => `${c(cid)}/slides/${page}/image`,
  cropUrl: (cid: string, tid: string) => `${c(cid)}/threads/${tid}/crop`,
  explain: (cid: string, page: number, regenerate = false) =>
    req<{ explanation?: Explanation; job?: JobInfo }>(
      'POST', `${c(cid)}/slides/${page}/explanation${regenerate ? '?regenerate=true' : ''}`),
  ask: (cid: string, page: number, anchor: { rect: Rect } | { quote: string }, question: string) =>
    req<{ thread: Thread; job: JobInfo }>('POST', `${c(cid)}/slides/${page}/threads`, { ...anchor, question }),
  followUp: (cid: string, tid: string, text: string) =>
    req<{ message: Message; job: JobInfo }>('POST', `${c(cid)}/threads/${tid}/messages`, { text }),
  retry: (cid: string, tid: string) => req<{ job: JobInfo }>('POST', `${c(cid)}/threads/${tid}/retry`),
  setMinimized: (cid: string, tid: string, minimized: boolean) =>
    req<{ ok: true }>('PATCH', `${c(cid)}/threads/${tid}`, { minimized }),
  profile: (folder: string) => req<Profile>('GET', `/api/library/profile?folder=${encodeURIComponent(folder)}`),
  organizeProfile: (folder: string) =>
    req<{ job: JobInfo }>('POST', `/api/library/profile/organize?folder=${encodeURIComponent(folder)}`),
  patchWeakPoint: (folder: string, wid: string, body: { status?: WeakStatus; concept?: string; summary?: string }) =>
    req<Profile>('PATCH', `/api/library/profile/${wid}?folder=${encodeURIComponent(folder)}`, body),
  mergeWeakPoint: (folder: string, wid: string, into: string) =>
    req<Profile>('POST', `/api/library/profile/${wid}/merge?folder=${encodeURIComponent(folder)}`, { into }),
  deleteWeakPoint: (folder: string, wid: string) =>
    req<Profile>('DELETE', `/api/library/profile/${wid}?folder=${encodeURIComponent(folder)}`),
  undoProfile: (folder: string) => req<Profile>('POST', `/api/library/profile/undo?folder=${encodeURIComponent(folder)}`),
  digest: (cid: string) => req<Digest>('GET', `${c(cid)}/digest`),
  makeDigest: (cid: string) => req<{ job: JobInfo }>('POST', `${c(cid)}/digest`),
  setUnderstood: (cid: string, tid: string, understood: boolean | null) =>
    req<{ ok: true }>('PATCH', `${c(cid)}/threads/${tid}`, { understood }),
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
