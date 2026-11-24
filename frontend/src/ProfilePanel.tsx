import { useCallback, useEffect, useState } from 'react'
import { api, followJob, type Evidence, type JobInfo, type Listing, type Profile, type WeakPoint, type WeakStatus } from './api'
import { Icon } from './Icon'
import { useT } from './i18n'
import { Markdown } from './Markdown'
import { go, rememberPath } from './route'

const AUTO = ['digest', 'organize', 'quiz', 'mark']

/** The course's study profile (DESIGN.md §15.4): weak points by concept, with evidence and history. */
export function ProfilePanel({ listing }: { listing: Listing }) {
  const t = useT()
  const folder = listing.path
  const [p, setP] = useState<Profile | null>(null)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [history, setHistory] = useState(false)
  const [showResolved, setShowResolved] = useState(false)

  const load = useCallback(() => api.profile(folder).then(setP).catch(() => {}), [folder])
  const follow = useCallback((job: JobInfo) => {
    setRunning(true)
    followJob(job.id, (ev) => {
      if (ev.type === 'done') { setRunning(false); load() }
      if (ev.type === 'error') { setRunning(false); setError(ev.message) }
    }).catch((e) => { setRunning(false); setError(String(e.message ?? e)) })
  }, [load])

  useEffect(() => {
    api.profile(folder).then((x) => { setP(x); if (x.job) follow(x.job) }).catch(() => {})
  }, [folder, follow])

  const act = async (f: () => Promise<Profile>) => {
    setError(null)
    try { setP(await f()) } catch (e) { setError(e instanceof Error ? e.message : String(e)) }
  }
  const organize = async () => {
    setError(null)
    try { follow((await api.organizeProfile(folder)).job) } catch (e) { setError(e instanceof Error ? e.message : String(e)) }
  }
  const openEvidence = async (e: Evidence) => {
    const lec = p?.lectures[e.lecture]
    if (!lec || !e.page) return
    try {
      const c = await api.open(lec.file)
      rememberPath(c.id, c.path)
      go(e.kind === 'thread' ? { name: 'slide', cid: c.id, page: e.page, thread: e.ref } : { name: 'slide', cid: c.id, page: e.page })
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  if (!p) return null
  const by = (s: WeakStatus) => p.weak_points.filter((w) => w.status === s)
  const latest = p.revisions.find((r) => !r.undone)
  const canUndo = !!latest && AUTO.includes(latest.trigger)
  const empty = !p.weak_points.length && !p.pending.length

  const item = (w: WeakPoint) => (
    <li key={w.id} className={'wp ' + w.status}>
      <div className="wp-head">
        <span className="wp-id">{w.id}</span>
        <Markdown text={w.concept} className="wp-concept" />
        {w.pinned && <span className="chip" title={t.pinnedTitle}>{t.pinned}</span>}
        <span className="spacer" />
        {w.status !== 'resolved'
          ? <button className="chipbtn got" onClick={() => act(() => api.patchWeakPoint(folder, w.id, { status: 'resolved' }))}>{t.markResolved}</button>
          : <button className="chipbtn confused" onClick={() => act(() => api.patchWeakPoint(folder, w.id, { status: 'open' }))}>{t.reopen}</button>}
        <WpMenu w={w} others={p.weak_points.filter((x) => x.id !== w.id)}
          onRename={(concept, summary) => act(() => api.patchWeakPoint(folder, w.id, { concept, summary }))}
          onMerge={(into) => act(() => api.mergeWeakPoint(folder, w.id, into))}
          onDelete={() => act(() => api.deleteWeakPoint(folder, w.id))} />
      </div>
      {w.summary && <Markdown text={w.summary} className="wp-summary" />}
      <div className="wp-ev">
        {w.evidence.map((e) => {
          const lec = p.lectures[e.lecture]
          const label = lec ? t.evidenceWhere(lec.number, e.page ?? 0) : t.evidenceGone
          return (
            <button key={e.id} className={'ev ' + (e.polarity > 0 ? 'bad' : 'good')} disabled={!lec || !e.page}
              title={(e.note || '') + (lec ? '' : ` · ${t.evidenceGone}`)} onClick={() => openEvidence(e)}>
              {e.kind === 'quiz' ? <Icon name="doc" /> : <Icon name="select" />}{label}
            </button>
          )
        })}
        {w.extra_evidence > 0 && <span className="row-meta">{t.moreEvidence(w.extra_evidence)}</span>}
      </div>
    </li>
  )

  return (
    <section className="card profile">
      <div className="lib-head">
        <h2>{t.studyProfile}</h2>
        <span className="spacer" />
        {p.pending.length > 0 && (
          <button className="btn" disabled={running} onClick={organize} title={t.organizeTitle}>
            {running ? <span className="spin" /> : <Icon name="refresh" />}{t.organize(p.pending.length)}
          </button>
        )}
        {canUndo && <button className="btn ghost" onClick={() => act(() => api.undoProfile(folder))}>{t.undoAuto}</button>}
        {p.revisions.length > 0 && (
          <button className="btn ghost" aria-pressed={history} onClick={() => setHistory((h) => !h)}>{t.history}</button>
        )}
      </div>
      {running && <p className="warn quiz-progress" role="status"><span className="spin" />{t.organizing}</p>}
      {error && <p className="err" role="alert">{error}</p>}
      {empty && <p className="tip">{t.profileEmpty}</p>}
      {(['open', 'improving'] as const).map((s) => by(s).length > 0 && (
        <div key={s}>
          <h3 className="sub">{t.wpStatus[s]} · {by(s).length}</h3>
          <ul className="wps">{by(s).map(item)}</ul>
        </div>
      ))}
      {by('resolved').length > 0 && (
        <div>
          <button className="linkbtn" onClick={() => setShowResolved((x) => !x)}>
            {t.wpStatus.resolved} · {by('resolved').length} {showResolved ? '▴' : '▾'}
          </button>
          {showResolved && <ul className="wps">{by('resolved').map(item)}</ul>}
        </div>
      )}
      {history && (
        <ul className="history">
          {p.revisions.map((r) => (
            <li key={r.id} className={r.undone ? 'undone' : ''}>
              <span className="row-meta">{r.created_at.slice(0, 16).replace('T', ' ')} · {t.trigger[r.trigger] ?? r.trigger}</span>
              <span>{r.changes.map((c) => describeChange(c, t)).join('; ')}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

function describeChange(c: Record<string, unknown>, t: ReturnType<typeof useT>): string {
  const s = (k: string) => String(c[k] ?? '')
  switch (c.op) {
    case 'add': return t.chAdd(s('id'), s('concept'))
    case 'status': return t.chStatus(s('id'), t.wpStatus[s('to') as WeakStatus] ?? s('to'))
    case 'merge': return t.chMerge(s('from'), s('into'))
    case 'edit': return t.chEdit(s('id'))
    case 'delete': return t.chDelete(s('id'))
    case 'attach': return t.chAttach(s('id'))
    case 'pending': return t.chPending(s('page'))
    default: return s('op')
  }
}

function WpMenu({ w, others, onRename, onMerge, onDelete }: {
  w: WeakPoint; others: WeakPoint[]
  onRename: (concept: string, summary: string) => void; onMerge: (into: string) => void; onDelete: () => void
}) {
  const t = useT()
  const [open, setOpen] = useState(false)
  return (
    <div className="menu row-menu">
      <button className="btn ghost icon-btn" aria-label={t.actions} aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        <Icon name="more" />
      </button>
      {open && (
        <div className="menu-pop list" role="menu" onMouseLeave={() => setOpen(false)}>
          <button role="menuitem" className="btn ghost" onClick={() => {
            setOpen(false)
            const concept = prompt(t.wpConcept, w.concept)?.trim()
            if (!concept) return
            const summary = prompt(t.wpSummary, w.summary)
            onRename(concept, summary ?? w.summary)
          }}>{t.rename}</button>
          {others.length > 0 && (
            <button role="menuitem" className="btn ghost" onClick={() => {
              setOpen(false)
              const into = prompt(t.mergePrompt(w.id, others.map((o) => `${o.id} ${o.concept}`).join('\n')))?.trim().toUpperCase()
              if (into && others.some((o) => o.id === into)) onMerge(into)
            }}>{t.mergeInto}</button>
          )}
          <button role="menuitem" className="btn ghost" onClick={() => { setOpen(false); if (confirm(t.deleteWpConfirm(w.id))) onDelete() }}>
            {t.delete}
          </button>
        </div>
      )}
    </div>
  )
}
