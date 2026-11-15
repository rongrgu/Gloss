import { useCallback, useEffect, useState } from 'react'
import { api, followJob, type JobEvent, type JobInfo, type Listing, type QuestionType, type QuizFocus, type QuizSummary } from './api'
import { Icon } from './Icon'
import { useT } from './i18n'
import { quizProgress } from './quizLogic'
import { href } from './route'

const TYPES: QuestionType[] = ['mcq', 'short', 'derivation']
const FOCI: QuizFocus[] = ['weak', 'balanced', 'uncovered']

/** Pre-quizzes for one folder (DESIGN.md §13.6): the list, a running generation, and the "new" dialog. */
export function QuizPanel({ listing }: { listing: Listing }) {
  const t = useT()
  const folder = listing.path
  const [quizzes, setQuizzes] = useState<QuizSummary[]>([])
  const [events, setEvents] = useState<JobEvent[] | null>(null) // non-null while generating
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [dialog, setDialog] = useState(false)

  const follow = useCallback((job: JobInfo, signal?: AbortSignal) => {
    const seen: JobEvent[] = []
    setEvents([])
    followJob(job.id, (ev) => {
      seen.push(ev)
      setEvents([...seen])
      if (ev.type === 'done') {
        setEvents(null)
        if (ev.count !== undefined && ev.requested !== undefined && ev.count < ev.requested) setNotice(t.quizShort(ev.count, ev.requested))
        api.quizzes(folder).then((r) => setQuizzes(r.quizzes)).catch(() => {})
      }
      if (ev.type === 'error') { setEvents(null); setError(t.quizFailed + ev.message) }
    }, signal).catch((e) => { if (!signal?.aborted) { setEvents(null); setError(String(e.message ?? e)) } })
  }, [folder, t])

  useEffect(() => {
    const ctrl = new AbortController()
    api.quizzes(folder).then((r) => {
      setQuizzes(r.quizzes)
      if (r.job) follow(r.job, ctrl.signal)
    }).catch(() => {})
    return () => ctrl.abort()
  }, [folder, follow])

  const start = async (body: Parameters<typeof api.startQuiz>[0]) => {
    setDialog(false)
    setError(null)
    setNotice(null)
    try {
      follow((await api.startQuiz(body)).job)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  return (
    <section className="card quizzes">
      <div className="lib-head">
        <h2>{t.preQuiz}</h2>
        <span className="spacer" />
        <button className="btn primary" disabled={!!events} onClick={() => setDialog(true)}><Icon name="plus" />{t.newQuiz}</button>
      </div>
      {events && <p className="warn quiz-progress" role="status"><span className="spin" />{quizProgress(events, t)}</p>}
      {notice && <p className="tip">{notice}</p>}
      {error && <p className="err" role="alert">{error}</p>}
      {quizzes.length > 0 && (
        <ul className="rows">
          {quizzes.map((q) => (
            <li key={q.id}>
              <a className="row-main" href={href({ name: 'quiz', folder, qid: q.id })}>
                <Icon name="doc" className="kind course" />
                <span className="row-txt">
                  <span className="row-title">{q.title}</span>
                  <span className="row-meta">{t.quizMeta(q.count, q.answered, q.correct)} · {q.created_at.slice(0, 10)}</span>
                </span>
              </a>
              <button className="btn ghost icon-btn" aria-label={t.delete} title={t.delete}
                onClick={() => confirm(t.deleteQuizConfirm(q.title))
                  && api.deleteQuiz(folder, q.id).then(() => setQuizzes((xs) => xs.filter((x) => x.id !== q.id)))}>
                <Icon name="trash" />
              </button>
            </li>
          ))}
        </ul>
      )}
      {dialog && <QuizDialog listing={listing} onClose={() => setDialog(false)} onStart={start} />}
    </section>
  )
}

function QuizDialog({ listing, onClose, onStart }: {
  listing: Listing; onClose: () => void; onStart: (b: Parameters<typeof api.startQuiz>[0]) => void
}) {
  const t = useT()
  const [count, setCount] = useState(10)
  const [types, setTypes] = useState<QuestionType[]>(TYPES)
  const [focus, setFocus] = useState<QuizFocus>('weak')
  // lecture choice only when the folder holds lectures directly and nothing nested (otherwise: everything)
  const choosable = !listing.folders.length && listing.lectures.length > 1
  const [picked, setPicked] = useState<string[]>(listing.lectures.map((l) => l.path))
  useEffect(() => {
    const esc = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    document.addEventListener('keydown', esc)
    return () => document.removeEventListener('keydown', esc)
  }, [onClose])
  const focusText = { weak: t.focusWeak, balanced: t.focusBalanced, uncovered: t.focusUncovered }
  const toggle = <T,>(xs: T[], x: T) => (xs.includes(x) ? xs.filter((y) => y !== x) : [...xs, x])
  const ok = types.length > 0 && count >= 1 && count <= 30 && (!choosable || picked.length > 0)
  return (
    <div className="modal-back" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <form className="dialog quiz-dialog" role="dialog" aria-modal="true" aria-label={t.newQuiz}
        onSubmit={(e) => {
          e.preventDefault()
          if (ok) onStart({ folder: listing.path, count, types, focus,
            lectures: choosable && picked.length < listing.lectures.length ? picked : null })
        }}>
        <h2>{t.newQuiz} · {listing.name || t.library}</h2>
        <label className="field">
          <span className="lbl">{t.quizCount}</span>
          <input type="number" min={1} max={30} value={count} onChange={(e) => setCount(Number(e.target.value))} />
        </label>
        <fieldset className="field">
          <legend className="lbl">{t.quizTypes}</legend>
          <div className="checks wrap">
            {TYPES.map((ty) => (
              <label key={ty} className="check">
                <input type="checkbox" checked={types.includes(ty)} onChange={() => setTypes((xs) => toggle(xs, ty))} />
                {t.typeName[ty]}
              </label>
            ))}
          </div>
        </fieldset>
        <fieldset className="field">
          <legend className="lbl">{t.quizFocus}</legend>
          <div className="choices">
            {FOCI.map((f) => (
              <label key={f} className={'choice' + (focus === f ? ' on' : '')}>
                <input type="radio" name="focus" checked={focus === f} onChange={() => setFocus(f)} />
                <span><b>{focusText[f][0]}</b><span className="row-meta">{focusText[f][1]}</span></span>
              </label>
            ))}
          </div>
        </fieldset>
        {choosable && (
          <fieldset className="field">
            <legend className="lbl">{t.quizLectures}</legend>
            <div className="choices">
              {listing.lectures.map((l, i) => (
                <label key={l.path} className="check">
                  <input type="checkbox" checked={picked.includes(l.path)}
                    onChange={() => setPicked((xs) => toggle(xs, l.path))} />
                  {i + 1}. {l.title}
                </label>
              ))}
            </div>
          </fieldset>
        )}
        <p className="tip">{t.quizCost}</p>
        <div className="dialog-actions">
          <button type="button" className="btn" onClick={onClose}>{t.cancel}</button>
          <button className="btn primary" disabled={!ok}>{t.generate}</button>
        </div>
      </form>
    </div>
  )
}

