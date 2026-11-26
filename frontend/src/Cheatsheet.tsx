import { EditorContent } from '@tiptap/react'
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { api, followJob, type JobInfo, type SheetVersion } from './api'
import { Brand, Icon } from './Icon'
import { LangSwitch, useT, type Messages } from './i18n'
import { Markdown } from './Markdown'
import { href } from './route'
import { FormatBar, FormulaDialog, useSheetDoc } from './sheetDoc'

// The print layout (styles.css, @media print): A4, 10mm margins, two columns 6mm apart, 9pt text.
const MM = 96 / 25.4
const COLUMN_PX = ((210 - 20 - 6) / 2) * MM
const PAGE_PX = (297 - 20) * MM

/** A cheatsheet (DESIGN.md §16.4): chat on the left to have Gloss change it; on the right the sheet itself,
 *  edited like a Word document (stored as Markdown underneath, which the student never has to see). */
export default function SheetPage({ folder, sid }: { folder: string; sid: string }) {
  const t = useT()
  const [request, setRequest] = useState('')
  const [selection, setSelection] = useState('')
  const [running, setRunning] = useState(false)
  const [history, setHistory] = useState(false)
  const [viewing, setViewing] = useState<{ v: SheetVersion; content: string } | null>(null)
  const [source, setSource] = useState(false) // the Markdown source instead of the formatted editor
  const [pages, setPages] = useState<number | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const measure = useRef<HTMLDivElement>(null)
  const msgsRef = useRef<HTMLDivElement>(null)
  const { editor, sheet, setSheet, content, saving, flush, load, apply, reload, onType, math, setMath, saveFormula, setEditable } =
    useSheetDoc({ folder, sid, onSelection: setSelection, onError: setError })

  const follow = useCallback((job: JobInfo) => {
    setRunning(true)
    followJob(job.id, (ev) => {
      if (ev.type === 'done') { setRunning(false); reload().catch(() => {}) }
      if (ev.type === 'error') { setRunning(false); setError(t.sheetReviseFailed + ev.message); reload().catch(() => {}) }
    }).catch((e) => { setRunning(false); setError(String(e.message ?? e)) })
  }, [reload, t])

  useEffect(() => {
    if (!editor) return
    api.sheet(folder, sid).then((s) => { apply(s); if (s.job) follow(s.job) })
      .catch((e) => setError(e?.status === 404 ? t.sheetNotFound : String(e.message ?? e)))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [folder, sid, editor])

  // read-only while Gloss is revising, while an old version is shown, or while the source is being edited
  useEffect(() => { setEditable(!running && !viewing && !source) }, [setEditable, running, viewing, source])
  useEffect(() => { if (viewing) load(viewing.content); else if (sheet) load(content) }, [viewing]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { // scroll the chat box only, never the page (on phones the chat is below the sheet)
    const el = msgsRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [sheet?.messages.length, running])

  // --- the page count in the print layout ---
  const shown = viewing ? viewing.content : content
  useLayoutEffect(() => {
    const h = measure.current?.scrollHeight
    setPages(h ? Math.max(0.1, h / (2 * PAGE_PX)) : null)
  }, [shown])

  const send = async () => {
    const r = request.trim()
    if (!r || running) return
    setError(null)
    await flush()
    try {
      const { job } = await api.reviseSheet(folder, sid, r, selection)
      setRequest('')
      setSheet((s) => s && { ...s, messages: [...s.messages, { id: -Date.now(), role: 'user', content: r + (selection ? `\n\n> ${selection.slice(0, 300)}` : ''), version_id: null, created_at: '' }] })
      setSelection('')
      follow(job)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }
  const restore = async (vid: number) => {
    await flush()
    try { setViewing(null); apply(await api.restoreSheet(folder, sid, vid)) } catch (e) { setError(e instanceof Error ? e.message : String(e)) }
  }
  const toggleSource = async () => {
    await flush()
    if (source) load(content)
    setSource((x) => !x)
  }
  const print = async () => {
    await flush()
    if (source) { load(content); setSource(false) }
    setViewing(null)
    window.setTimeout(() => window.print(), 80)
  }
  const downloadWord = async () => {
    await flush()
    setNotice(null)
    try {
      const r = await fetch(api.sheetDocxUrl(folder, sid))
      if (!r.ok) throw new Error(r.statusText)
      const blob = await r.blob()
      const a = document.createElement('a')
      a.href = URL.createObjectURL(blob)
      a.download = `${(sheet?.title || 'cheatsheet').replace(/[\\/:*?"<>|]/g, '_')}.docx`
      a.click()
      URL.revokeObjectURL(a.href)
      if (r.headers.get('X-Gloss-Equations') === 'text') setNotice(t.wordNoEquations)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  const back = href({ name: 'home', folder })
  const header = (
    <header className="bar">
      <a className="btn ghost" href={back} style={{ textDecoration: 'none' }} title={t.upOneLevel}><Brand /></a>
      <a className="file" href={back}>{folder || t.library}</a>
      {sheet && <span className="file wide-only">· {sheet.title}</span>}
      <span className="spacer" />
      <LangSwitch />
    </header>
  )
  if (!sheet) {
    return (
      <>{header}<main className="home">{error ? <><p className="err">{error}</p><a className="btn" href={back}>{t.backToFolder}</a></> : <p className="lead">{t.opening}</p>}</main>
        <div hidden><EditorContent editor={editor} /></div></>
    )
  }

  const target = sheet.options.pages
  const prev = sheet.versions.length > 1 ? sheet.versions[sheet.versions.length - 2] : null
  const editable = !running && !viewing && !source
  return (
    <>
      {header}
      <main className="sheet-page">
        <aside className="sheet-chat" aria-label={t.sheetChat}>
          <div className="sheet-msgs" ref={msgsRef}>
            <p className="tip">{t.sheetChatIntro}</p>
            {sheet.messages.map((m) => (
              <div key={m.id} className={'sheet-msg ' + m.role}>
                {m.role === 'assistant' ? <Markdown text={m.content} /> : <p>{m.content}</p>}
                {m.role === 'assistant' && m.version_id !== null && <span className="row-meta">✓ {t.sheetChanged}</span>}
              </div>
            ))}
            {running && <p className="warn quiz-progress" role="status"><span className="spin" />{t.sheetRevising}</p>}
          </div>
          <form className="sheet-ask" onSubmit={(e) => { e.preventDefault(); send() }}>
            {selection && (
              <div className="sel-chip">
                <span>{t.sheetOnlySelection}</span>
                <q>{selection.length > 80 ? selection.slice(0, 80) + '…' : selection}</q>
                <button type="button" className="btn ghost icon-btn" aria-label={t.close} onClick={() => setSelection('')}><Icon name="close" /></button>
              </div>
            )}
            <textarea rows={3} value={request} placeholder={t.sheetAskPlaceholder} disabled={running}
              onChange={(e) => setRequest(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); send() } }} />
            <button className="btn primary" disabled={running || !request.trim()}><Icon name="send" />{t.send}</button>
          </form>
        </aside>

        <section className="sheet-main">
          <div className="sheet-toolbar">
            <button className="btn ghost" disabled={!prev || running} onClick={() => prev && restore(prev.id)} title={t.sheetUndoTitle}>{t.undoChange}</button>
            <button className="btn ghost" aria-pressed={history} onClick={() => setHistory((h) => !h)}>{t.history} · {sheet.versions.length}</button>
            <span className="spacer" />
            {saving !== 'idle' && <span className="row-meta">{saving === 'pending' ? t.saving : t.savedShort}</span>}
            {pages !== null && (
              <span className={'chip' + (target && pages > target + 0.05 ? ' over' : '')} title={t.sheetPagesTitle}>
                {t.sheetPages(pages, target)}
              </span>
            )}
            <button className="btn" onClick={print}>{t.print}</button>
            <button className="btn primary" onClick={downloadWord}>{t.downloadWord}</button>
            <MoreMenu t={t} source={source} onSource={toggleSource} mdUrl={api.sheetMdUrl(folder, sid)} />
          </div>
          {editor && editable && <FormatBar editor={editor} t={t} onFormula={() => setMath({ pos: null, latex: '', block: false })} />}
          {notice && <p className="tip">{notice}</p>}
          {error && <p className="err" role="alert">{error}</p>}
          {history && (
            <ul className="sheet-history">
              {[...sheet.versions].reverse().map((v) => (
                <li key={v.id} className={viewing?.v.id === v.id ? 'on' : v.id === sheet.version ? 'current' : ''}>
                  <button className="linkbtn" onClick={async () => {
                    if (v.id === sheet.version) { setViewing(null); return }
                    await flush()
                    try { setViewing({ v, content: (await api.sheetVersion(folder, sid, v.id)).content }) } catch { /* gone */ }
                  }}>
                    {v.created_at.slice(5, 16).replace('T', ' ')} · {t.sheetSource[v.source]}{v.note && v.source === 'ai' ? `：${v.note}` : ''}
                  </button>
                  {v.id === sheet.version && <span className="row-meta"> · {t.sheetCurrent}</span>}
                </li>
              ))}
            </ul>
          )}
          {viewing && (
            <p className="warn sheet-viewing">
              {t.sheetViewing(viewing.v.created_at.slice(5, 16).replace('T', ' '))}
              <button className="btn primary" onClick={() => restore(viewing.v.id)}>{t.sheetRestore}</button>
              <button className="btn ghost" onClick={() => setViewing(null)}>{t.sheetBackToCurrent}</button>
            </p>
          )}
          <div className={'sheet-doc' + (source ? ' editing' : '') + (editable ? ' live' : '')}>
            {source
              ? <textarea className="sheet-src" value={content} spellCheck={false} autoFocus
                  onChange={(e) => onType(e.target.value)} onBlur={() => flush()} />
              : <EditorContent editor={editor} />}
          </div>
          {editable && <p className="tip">{t.sheetEditHint}</p>}
          <div className="sheet-measure" ref={measure} style={{ width: COLUMN_PX }} aria-hidden>
            <Markdown text={shown} className="sheet-md print" />
          </div>
        </section>
      </main>
      {math && <FormulaDialog t={t} value={math} onClose={() => setMath(null)} onSave={saveFormula} />}
    </>
  )
}

function MoreMenu({ t, source, onSource, mdUrl }: { t: Messages; source: boolean; onSource: () => void; mdUrl: string }) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const close = (e: Event) => {
      if (e instanceof KeyboardEvent ? e.key === 'Escape' : !ref.current?.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('pointerdown', close)
    document.addEventListener('keydown', close)
    return () => { document.removeEventListener('pointerdown', close); document.removeEventListener('keydown', close) }
  }, [open])
  return (
    <div className="menu" ref={ref}>
      <button className="btn ghost icon-btn" aria-label={t.more} title={t.more} aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        <Icon name="more" />
      </button>
      {open && (
        <div className="menu-pop list" role="menu">
          <button role="menuitem" className="btn ghost" onClick={() => { setOpen(false); onSource() }}>
            {source ? t.backToRich : t.sourceMode}
          </button>
          <a role="menuitem" className="btn ghost" href={mdUrl} download onClick={() => setOpen(false)}>{t.downloadMd}</a>
        </div>
      )}
    </div>
  )
}
