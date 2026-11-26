import { useEffect, useState } from 'react'
import { api, type CatchUpData } from './api'
import { Icon } from './Icon'
import { useT, type Messages } from './i18n'
import { go, href, rememberPath } from './route'

/** How long ago, in words ("3 days ago"). */
export function ago(iso: string, t: Messages, now = Date.now()): string {
  const ms = now - Date.parse(iso)
  if (!Number.isFinite(ms)) return ''
  const min = Math.round(ms / 60000)
  if (min < 2) return t.agoNow
  if (min < 60) return t.agoMinutes(min)
  const h = Math.round(min / 60)
  if (h < 24) return t.agoHours(h)
  const d = Math.round(h / 24)
  return d < 30 ? t.agoDays(d) : t.agoLong(iso.slice(0, 10))
}

/** The library home's "where you left off" (DESIGN.md §17): read from disk, no AI call. */
export function CatchUp() {
  const t = useT()
  const [data, setData] = useState<CatchUpData | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => { api.catchup().then(setData).catch(() => {}) }, [])
  if (!data) return null
  const { last } = data
  const nothing = !last && !data.quizzes.length && !data.confused.length && !data.sheet
  if (nothing) return null

  const open = async (file: string, page: number | null, thread?: string) => {
    setError(null)
    try {
      const c = await api.open(file)
      rememberPath(c.id, c.path)
      if (!page) go({ name: 'overview', cid: c.id })
      else go(thread ? { name: 'slide', cid: c.id, page, thread } : { name: 'slide', cid: c.id, page })
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }
  const course = (folder: string) => folder || t.library

  return (
    <section className="card catchup">
      <h2>{t.catchUpH}</h2>
      {last && (
        <div className="cu-last">
          <p>{t.catchUpLast(ago(last.active_at, t), course(last.folder), last.title, last.page, last.slides)}</p>
          <button className="btn primary" onClick={() => open(last.file, last.page)}>
            {last.page ? t.continueAt(last.page) : t.openLecture}<Icon name="next" />
          </button>
        </div>
      )}
      <ul className="cu-items">
        {data.confused_total > 0 && (
          <li>
            <span className="cu-what">{t.cuConfused(data.confused_total)}</span>
            <span className="cu-links">
              {data.confused.slice(0, 3).map((c) => (
                <button key={c.thread} className="linkbtn" onClick={() => open(c.file, c.page, c.thread)}
                  title={`${c.lecture} · ${t.pageN(c.page)}`}>
                  “{c.question.length > 60 ? c.question.slice(0, 60) + '…' : c.question}”
                </button>
              ))}
            </span>
          </li>
        )}
        {data.quizzes.map((q) => (
          <li key={q.id}>
            <span className="cu-what">{t.cuQuiz(q.answered, q.count)}</span>
            <a className="linkbtn" href={href({ name: 'quiz', folder: q.folder, qid: q.id })}>{q.title}</a>
            <span className="row-meta">{course(q.folder)}</span>
          </li>
        ))}
        {data.weak_total > 0 && (
          <li>
            <span className="cu-what">{t.cuWeak(data.weak_total)}</span>
            <span className="cu-links">
              {data.weak_points.map((w, i) => (
                <a key={i} className="linkbtn" href={href({ name: 'home', folder: w.folder })}>{w.concept}</a>
              ))}
            </span>
          </li>
        )}
        {data.continue.length > 0 && (
          <li>
            <span className="cu-what">{t.cuAlso}</span>
            <span className="cu-links">
              {data.continue.map((l) => (
                <button key={l.file} className="linkbtn" onClick={() => open(l.file, l.page)}>
                  {l.title}{l.page ? ` · ${t.pageN(l.page)}` : ''}
                </button>
              ))}
            </span>
          </li>
        )}
        {data.sheet && (
          <li>
            <span className="cu-what">{t.cuSheet}</span>
            <a className="linkbtn" href={href({ name: 'sheet', folder: data.sheet.folder, sid: data.sheet.id })}>{data.sheet.title}</a>
            <span className="row-meta">{ago(data.sheet.updated_at, t)}</span>
          </li>
        )}
      </ul>
      {error && <p className="err" role="alert">{error}</p>}
    </section>
  )
}
