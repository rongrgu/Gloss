import { useCallback, useEffect, useState } from 'react'
import {
  api, followJob, type JobEvent, type JobInfo, type Listing, type PastExam, type QuestionType, type QuizFocus, type QuizMode,
  type QuizSummary,
} from './api'
import { Icon } from './Icon'
import { useT } from './i18n'
import { quizProgress } from './quizLogic'
import { href } from './route'
import { RowMenu } from './RowMenu'

const TYPES: QuestionType[] = ['mcq', 'short', 'derivation']
const FOCI: QuizFocus[] = ['weak', 'balanced', 'uncovered']

/** Quizzes for one folder (DESIGN.md §13.6, §13.8): the generated quizzes, a running generation, and the "new" dialog. */
/** `home`: the library home's list of every course's quizzes, to take them from there; quizzes are made inside a course (§17). */
export function QuizPanel({ listing, home = false }: { listing: Listing; home?: boolean }) {
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
        if (ev.count !== undefined && ev.requested != null && ev.count < ev.requested) setNotice(t.quizShort(ev.count, ev.requested))
        api.quizzes(folder, home).then((r) => setQuizzes(r.quizzes)).catch(() => {})
      }
      if (ev.type === 'error') { setEvents(null); setError(t.quizFailed + ev.message) }
    }, signal).catch((e) => { if (!signal?.aborted) { setEvents(null); setError(String(e.message ?? e)) } })
  }, [folder, t])

  useEffect(() => {
    const ctrl = new AbortController()
    api.quizzes(folder, home).then((r) => {
      setQuizzes(r.quizzes)
      if (r.job && !home) follow(r.job, ctrl.signal)
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
    <section className={'card quizzes' + (home ? ' home-quizzes' : '')}>
      <div className="lib-head">
        <h2>{t.quizTitle}</h2>
        <span className="spacer" />
        {!home && <button className="btn primary" disabled={!!events} onClick={() => setDialog(true)}><Icon name="plus" />{t.newQuiz}</button>}
      </div>
      <div className="card-body">
      {events && <p className="warn quiz-progress" role="status"><span className="spin" />{quizProgress(events, t)}</p>}
      {notice && <p className="tip">{notice}</p>}
      {error && <p className="err" role="alert">{error}</p>}
      {home && quizzes.length === 0 && <p className="tip">{t.homeQuizEmpty}</p>}
      {quizzes.length > 0 && (
        <ul className="rows">
          {quizzes.map((q) => (
            <li key={q.id}>
              <a className="row-main" href={href({ name: 'quiz', folder: q.folder ?? folder, qid: q.id })}>
                <Icon name="doc" className="kind course" />
                <span className="row-txt">
                  <span className="row-title">{q.title}</span>
                  <span className="row-meta">{t.quizMeta(q.count, q.answered, q.correct)}{q.options.exam ? ` · ${t.replicatedFrom(q.options.exam.title)}` : ''}{home && q.folder ? ` · ${q.folder}` : ''} · {q.created_at.slice(0, 10)}</span>
                </span>
              </a>
              <RowMenu label={t.actions} items={[
                { label: t.rename, onClick: () => {
                  const title = prompt(t.newName, q.title)?.trim()
                  if (title && title !== q.title) {
                    api.renameQuiz(q.folder ?? folder, q.id, title).then(() => setQuizzes((xs) => xs.map((x) => (x.id === q.id ? { ...x, title } : x))))
                      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
                  }
                } },
                { label: t.delete, onClick: () => {
                  if (confirm(t.deleteQuizConfirm(q.title))) api.deleteQuiz(q.folder ?? folder, q.id).then(() => setQuizzes((xs) => xs.filter((x) => x.id !== q.id)))
                } },
              ]} />
            </li>
          ))}
        </ul>
      )}
      {dialog && <QuizDialog listing={listing} onClose={() => setDialog(false)} onStart={start} />}
      </div>
    </section>
  )
}

function QuizDialog({ listing, onClose, onStart }: {
  listing: Listing; onClose: () => void; onStart: (b: Parameters<typeof api.startQuiz>[0]) => void
}) {
  const t = useT()
  const [mode, setMode] = useState<QuizMode>('new')
  const [exams, setExams] = useState<PastExam[]>([]) // Materials › Practice Exam / Quiz, here and in subfolders
  const [exam, setExam] = useState('')
  useEffect(() => {
    api.exams(listing.path).then((r) => { setExams(r.exams); setExam((x) => x || r.exams[0]?.id || '') }).catch(() => {})
  }, [listing.path])
  const [instructions, setInstructions] = useState('')
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
  const replicate = mode === 'replicate'
  const ok = (replicate ? !!exam : types.length > 0 && count >= 1 && count <= 30)
    && (!choosable || picked.length > 0) && instructions.length <= 2000
  return (
    <div className="modal-back" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <form className="dialog quiz-dialog" role="dialog" aria-modal="true" aria-label={t.newQuiz}
        onSubmit={(e) => {
          e.preventDefault()
          if (ok) onStart({ folder: listing.path, count, types, focus, mode, exam: replicate ? exam : null,
            instructions: instructions.trim(),
            lectures: choosable && picked.length < listing.lectures.length ? picked : null })
        }}>
        <h2>{t.newQuiz} · {listing.name || t.library}</h2>
        <fieldset className="field">
          <legend className="lbl">{t.quizMode}</legend>
          <div className="choices">
            {(['new', 'replicate'] as QuizMode[]).map((m) => (
              <label key={m} className={'choice' + (mode === m ? ' on' : '') + (m === 'replicate' && !exams.length ? ' off' : '')}>
                <input type="radio" name="mode" checked={mode === m} disabled={m === 'replicate' && !exams.length}
                  onChange={() => setMode(m)} />
                <span><b>{t.modeName[m][0]}</b>
                  <span className="row-meta">{m === 'replicate' && !exams.length ? t.replicateNoExams : t.modeName[m][1]}</span></span>
              </label>
            ))}
          </div>
        </fieldset>
        {replicate && (
          <label className="field">
            <span className="lbl">{t.replicateWhich}</span>
            <select value={exam} onChange={(e) => setExam(e.target.value)}>
              {exams.map((e) => <option key={e.id} value={e.id}>{e.title} · {t.examMeta(e.pages)}</option>)}
            </select>
          </label>
        )}
        {!replicate && <>
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
        </>}
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
        <label className="field">
          <span className="lbl-row">
            <span className="lbl">{t.quizInstructions}</span>
            <button type="button" className="linkbtn" onClick={() => setInstructions(t.quizInstructionsExample)}>
              {t.useExample}
            </button>
          </span>
          <textarea rows={4} maxLength={2000} value={instructions} placeholder={t.quizInstructionsExample}
            onChange={(e) => setInstructions(e.target.value)} />
          <span className="row-meta">{t.quizInstructionsHint}</span>
        </label>
        <p className="tip">{t.quizCost}</p>
        <div className="dialog-actions">
          <button type="button" className="btn" onClick={onClose}>{t.cancel}</button>
          <button className="btn primary" disabled={!ok}>{t.generate}</button>
        </div>
      </form>
    </div>
  )
}

