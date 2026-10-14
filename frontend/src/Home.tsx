import { useEffect, useRef, useState } from 'react'
import { api, type Course, type RecentItem } from './api'
import { Brand } from './Icon'
import { LangSwitch, useT } from './i18n'

export default function Home({ onOpen, notice }: { onOpen: (c: Course) => void; notice?: string }) {
  const t = useT()
  const [recent, setRecent] = useState<RecentItem[]>([])
  const [health, setHealth] = useState<{ claude_found: boolean; logged_in: boolean | null; model: string } | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [over, setOver] = useState(false)
  const [path, setPath] = useState('')
  const fileRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    api.recent().then(setRecent).catch(() => {})
    api.health().then(setHealth).catch(() => setError(t.backendDown))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const run = async (label: string, f: () => Promise<Course>) => {
    setBusy(label)
    setError(null)
    try {
      onOpen(await f())
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }
  const importFile = (file: File | undefined) => {
    if (!file) return
    if (!/\.pdf$/i.test(file.name)) return setError(t.pdfOnly)
    run(t.importing(file.name), () => api.importPdf(file))
  }

  return (
    <>
      <header className="bar"><Brand /><span className="spacer" /><span className="saved wide-only">{t.savedLocally}</span><LangSwitch /></header>
      <main className="home">
        <div>
          <h1>{t.heroTitle}</h1>
          <p className="lead">{t.heroLead}</p>
        </div>
        {notice && <p className="warn">{notice}</p>}
        {health && !health.claude_found && (
          <p className="warn">{t.noClaude[0]}<code>claude</code>{t.noClaude[1]}</p>
        )}
        {health?.claude_found && health.logged_in === false && (
          <p className="warn">{t.notLoggedIn[0]}<code>claude</code>{t.notLoggedIn[1]}</p>
        )}
        <section className="card">
          <h2>{t.importTitle}</h2>
          <div
            className={'drop' + (over ? ' over' : '')}
            role="button" tabIndex={0}
            onClick={() => fileRef.current?.click()}
            onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && fileRef.current?.click()}
            onDragOver={(e) => { e.preventDefault(); setOver(true) }}
            onDragLeave={() => setOver(false)}
            onDrop={(e) => { e.preventDefault(); setOver(false); importFile(e.dataTransfer.files[0]) }}
          >
            {busy ?? t.dropHint}
          </div>
          <input ref={fileRef} type="file" accept="application/pdf,.pdf" hidden
            onChange={(e) => { importFile(e.target.files?.[0]); e.target.value = '' }} />
        </section>
        {recent.length > 0 && (
          <section className="card">
            <h2>{t.recent}</h2>
            <ul className="recent">
              {recent.map((r) => (
                <li key={r.path}>
                  <button disabled={!r.exists || !!busy} onClick={() => run(t.opening, () => api.open(r.path))}
                    title={r.exists ? r.path : t.fileMissing}>
                    <span>{r.title || t.untitled}</span>
                    <span className="p">{r.path}</span>
                  </button>
                </li>
              ))}
            </ul>
          </section>
        )}
        <section className="card">
          <h2>{t.openExisting}</h2>
          <form className="inline" onSubmit={(e) => { e.preventDefault(); if (path.trim()) run(t.opening, () => api.open(path.trim())) }}>
            <input value={path} onChange={(e) => setPath(e.target.value)} placeholder="/Users/you/Gloss/lecture-07.gloss" aria-label={t.filePath} />
            <button className="btn" disabled={!path.trim() || !!busy}>{t.open}</button>
          </form>
        </section>
        {error && <p className="err" role="alert">{error}</p>}
      </main>
    </>
  )
}
