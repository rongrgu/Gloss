import { useEffect, useState } from 'react'
import { api, type Quiz, type QuizQuestion, type QuizSource } from './api'
import { Brand, Icon } from './Icon'
import { LangSwitch, useT } from './i18n'
import { Markdown } from './Markdown'
import { LETTERS, mcqCorrect, resumeIndex, summarize } from './quizLogic'
import { go, href, rememberPath } from './route'

const POS_KEY = (qid: string) => `gloss.quiz.${qid}`

/** Taking a pre-quiz, one question at a time, then the results (DESIGN.md §13.6). */
export default function QuizPage({ folder, qid }: { folder: string; qid: string }) {
  const t = useT()
  const [quiz, setQuiz] = useState<Quiz | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [idx, setIdx] = useState(0) // questions.length = the results view
  const [fresh, setFresh] = useState<Set<string>>(new Set()) // "start over": ignore earlier attempts
  const [revealed, setRevealed] = useState<Set<string>>(new Set())
  const [drafts, setDrafts] = useState<Record<string, string>>({})

  useEffect(() => {
    api.quiz(folder, qid).then((q) => {
      setQuiz(q)
      let saved: number | null = null
      try { saved = Number(localStorage.getItem(POS_KEY(qid)) ?? 'NaN') } catch { /* storage unavailable */ }
      setIdx(saved !== null && Number.isInteger(saved) && saved >= 0 && saved <= q.questions.length ? saved : resumeIndex(q))
    }).catch((e) => setError(e?.status === 404 ? t.quizNotFound : String(e.message ?? e)))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [folder, qid])

  const loaded = quiz !== null
  useEffect(() => {
    if (!loaded) return // don't overwrite the saved position before it has been read
    try { localStorage.setItem(POS_KEY(qid), String(idx)) } catch { /* storage unavailable */ }
  }, [qid, idx, loaded])

  const back = href({ name: 'home', folder })
  const header = (
    <header className="bar">
      <a className="btn ghost" href={back} style={{ textDecoration: 'none' }}><Brand /></a>
      <a className="file" href={back}>{folder || t.library}</a>
      {quiz && <span className="file wide-only">· {quiz.title}</span>}
      <span className="spacer" />
      <LangSwitch />
    </header>
  )
  if (error) return <>{header}<main className="home"><p className="err">{error}</p><a className="btn" href={back}>{t.backToFolder}</a></main></>
  if (!quiz) return <>{header}<main className="home"><p className="lead">{t.opening}</p></main></>

  const n = quiz.questions.length
  const attemptOf = (q: QuizQuestion) => (fresh.has(q.id) ? null : q.attempt)
  const record = async (q: QuizQuestion, answer: string, correct: boolean | null) => {
    try {
      const a = await api.attempt(folder, qid, q.id, answer, correct)
      setFresh((s) => { const x = new Set(s); x.delete(q.id); return x })
      setQuiz((z) => z && { ...z, questions: z.questions.map((x) => (x.id === q.id ? { ...x, attempt: a } : x)) })
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }
  const openSource = async (s: QuizSource) => {
    if (!s.file) return
    try {
      const c = await api.open(s.file)
      rememberPath(c.id, c.path)
      go({ name: 'slide', cid: c.id, page: s.page })
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }
  const startOver = () => {
    setFresh(new Set(quiz.questions.map((q) => q.id)))
    setRevealed(new Set())
    setDrafts({})
    setIdx(0)
  }

  if (idx >= n) {
    const live = { ...quiz, questions: quiz.questions.map((q) => ({ ...q, attempt: attemptOf(q) })) }
    const s = summarize(live)
    return (
      <>
        {header}
        <main className="home quiz">
          <h1>{t.resultsH}</h1>
          <p className="lead">{t.score(s.right, s.total)}{s.unmarked ? ` · ${t.unmarkedCount(s.unmarked)}` : ''}</p>
          <section className="card">
            <h2>{t.byTopic}</h2>
            <ul className="topics">
              {s.byTopic.map((x) => (
                <li key={x.topic}>
                  <span>{x.topic}</span>
                  <span className="bar-track"><span className="bar-fill" style={{ width: `${(x.right / x.total) * 100}%` }} /></span>
                  <span className="row-meta">{x.right}/{x.total}</span>
                </li>
              ))}
            </ul>
          </section>
          <section className="card">
            <h2>{t.toReview}</h2>
            {s.wrong.length === 0 ? <p className="tip">{t.allRight}</p> : (
              <ul className="rows">
                {s.wrong.map((q) => (
                  <li key={q.id}>
                    <button className="row-main" onClick={() => setIdx(q.n - 1)}>
                      <span className="num">{q.n}</span>
                      <span className="row-txt"><span className="row-title">{q.topic}</span>
                        <span className="row-meta">{q.sources.map((x) => t.openSource(x.label, x.title, x.page)).join(' · ')}</span></span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>
          <div className="dialog-actions">
            <button className="btn" onClick={startOver}>{t.startOver}</button>
            <a className="btn primary" href={back} style={{ textDecoration: 'none' }}>{t.backToFolder}</a>
          </div>
        </main>
      </>
    )
  }

  const q = quiz.questions[idx]
  const attempt = attemptOf(q)
  const shown = q.type === 'mcq' ? !!attempt : revealed.has(q.id) || !!attempt
  return (
    <>
      {header}
      <main className="home quiz">
        <div className="quiz-top">
          <span className="row-meta">{t.questionOf(idx + 1, n)}</span>
          <span className="progress"><span style={{ width: `${((idx + 1) / n) * 100}%` }} /></span>
        </div>
        <section className="card qcard">
          <div className="chips">
            <span className="chip">{t.typeName[q.type]}</span>
            <span className="chip">{q.topic}</span>
            <span className="chip">{t.difficultyName[q.difficulty - 1] ?? ''}</span>
          </div>
          {q.review && <p className="warn">{t.needsReview}{q.review}</p>}
          <Markdown text={q.stem} className="stem" />

          {q.type === 'mcq' ? (
            <div className="options" role="group">
              {q.options.map((o, i) => {
                const L = LETTERS[i]
                const cls = !attempt ? '' : mcqCorrect(q, L) ? ' right' : attempt.answer === L ? ' wrong' : ''
                return (
                  <button key={L} className={'option' + cls} disabled={!!attempt}
                    onClick={() => record(q, L, mcqCorrect(q, L))}>
                    <span className="letter">{L}</span><Markdown text={o} />
                  </button>
                )
              })}
              {attempt && (
                <p className={'verdict ' + (attempt.correct ? 'right' : 'wrong')}>
                  {attempt.correct ? t.correctMsg : t.wrongMsg(q.answer)}
                </p>
              )}
            </div>
          ) : (
            <div className="free">
              <label className="field">
                <span className="lbl">{t.yourAnswer}</span>
                <textarea rows={4} value={drafts[q.id] ?? attempt?.answer ?? ''}
                  onChange={(e) => setDrafts((d) => ({ ...d, [q.id]: e.target.value }))} />
              </label>
              {!shown && (
                <button className="btn primary" onClick={() => setRevealed((s) => new Set(s).add(q.id))}>{t.showAnswer}</button>
              )}
            </div>
          )}

          {shown && (
            <div className="reveal">
              {q.type !== 'mcq' && (
                <>
                  <h3>{t.referenceAnswer}</h3>
                  <Markdown text={q.answer} />
                  <div className="mark">
                    {[true, false].map((right) => (
                      <button key={String(right)} aria-pressed={attempt?.correct === (right ? 1 : 0)}
                        className={'btn' + (attempt?.correct === (right ? 1 : 0) ? (right ? ' right' : ' wrong') : '')}
                        onClick={() => record(q, drafts[q.id] ?? attempt?.answer ?? '', right)}>
                        {right ? t.markRight : t.markWrong}
                      </button>
                    ))}
                  </div>
                </>
              )}
              <h3>{t.explanationH}</h3>
              <Markdown text={q.explanation} />
              <h3>{t.whyH}</h3>
              <Markdown text={q.why} className="why" />
              <h3>{t.sourcesH}</h3>
              <div className="sources">
                {q.sources.map((s) => (
                  <button key={`${s.label}-${s.page}`} className="btn" disabled={!s.file}
                    title={s.file ? undefined : t.sourceMissing} onClick={() => openSource(s)}>
                    <Icon name="doc" />{t.openSource(s.label, s.title, s.page)}
                  </button>
                ))}
              </div>
            </div>
          )}
        </section>
        <div className="dialog-actions">
          <button className="btn" disabled={idx === 0} onClick={() => setIdx(idx - 1)}><Icon name="back" />{t.prevQ}</button>
          <span className="spacer" />
          <button className="btn primary" onClick={() => setIdx(idx + 1)}>
            {idx === n - 1 ? t.finish : t.nextQ}<Icon name="next" />
          </button>
        </div>
      </main>
    </>
  )
}
