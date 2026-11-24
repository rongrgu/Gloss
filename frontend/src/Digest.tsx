import { useEffect, useState } from 'react'
import { api, type Course, type Digest } from './api'
import { Icon } from './Icon'
import { LangSwitch, useT } from './i18n'
import { Markdown } from './Markdown'
import { href } from './route'
import { useDigestJob } from './digestJob'

/** A lecture's study summary (DESIGN.md §15.3). Links go to the slide and open that question. */
export default function DigestPage({ course, onRefresh }: { course: Course; onRefresh: () => void }) {
  const t = useT()
  const [digest, setDigest] = useState<Digest | null>(null)
  const [missing, setMissing] = useState(false)
  const job = useDigestJob(course, (d) => { setDigest(d); onRefresh() })

  useEffect(() => {
    api.digest(course.id).then(setDigest).catch(() => setMissing(true))
  }, [course.id])

  const fresh = digest?.new_messages ?? 0
  return (
    <>
      <header className="bar">
        <a className="btn ghost" href={href({ name: 'overview', cid: course.id })} style={{ textDecoration: 'none' }}>
          <Icon name="back" />{t.map}
        </a>
        <span className="file">{course.title}</span>
        <span className="spacer" />
        {digest && (
          <button className="btn" disabled={job.running} onClick={job.start}>
            <Icon name="refresh" />{job.running ? t.summarizing : fresh ? t.updateDigest(fresh) : t.redoDigest}
          </button>
        )}
        <LangSwitch />
      </header>
      <main className="home digest">
        {job.error && <p className="err" role="alert">{t.digestFailed}{job.error}</p>}
        {job.running && <p className="warn quiz-progress" role="status"><span className="spin" />{t.summarizing}</p>}
        {digest ? (
          <>
            <p className="row-meta">{t.digestMeta(digest.created_at.slice(0, 16).replace('T', ' '), digest.model ?? '')}
              {fresh > 0 && ` · ${t.newSinceDigest(fresh)}`}</p>
            <article className="card"><Markdown text={digest.content} className="digest-body" /></article>
          </>
        ) : missing && !job.running ? (
          <section className="card">
            <p>{t.noDigest}</p>
            <button className="btn primary" onClick={job.start}>{t.finishLecture}</button>
          </section>
        ) : null}
      </main>
    </>
  )
}
